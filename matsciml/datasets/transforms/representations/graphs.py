from typing import List, Union, Optional, Tuple
from logging import getLogger
from warnings import warn

import torch
import numpy as np

from matsciml.common import DataDict, package_registry
from matsciml.common.types import DataDict, GraphTypes, AbstractGraph
from matsciml.datasets.transforms.representations import RepresentationTransform
from matsciml.datasets.utils import retrieve_pointcloud_node_types

"""
Construct graphs from point clouds
"""

__all__ = ["PointCloudToGraphTransform"]

if package_registry["dgl"]:
    import dgl
    from dgl import DGLGraph
    from dgl import graph as dgl_graph

if package_registry["pyg"]:
    import torch_geometric
    from torch_geometric.data import Data as PyGGraph

log = getLogger(__name__)


class PointCloudToGraphTransform(RepresentationTransform):
    def __init__(
        self,
        backend: str,
        cutoff_dist: float = 7.0,
        node_keys: List[str] = ["atomic_numbers", "force"],
        edge_keys: Optional[List[str]] = None,
    ) -> None:
        super().__init__(backend=backend)
        self.cutoff_dist = cutoff_dist
        self.node_keys = node_keys
        self.edge_keys = edge_keys

    @property
    def node_keys(self) -> List[str]:
        return self._node_keys

    @node_keys.setter
    def node_keys(self, values: List[str]) -> None:
        values = set(values)
        self._node_keys = list(values)

    def prologue(self, data: DataDict) -> None:
        assert not self._check_for_type(
            data, GraphTypes
        ), "Data structure already contains a graph: transform shouldn't be required."
        # check for keys needed to construct the graph
        assert "pos" in data, f"No atomic positions 'pos' key present in data sample."
        has_atom_key = False
        for key in ["atomic_numbers", "pc_features"]:
            if key in data:
                has_atom_key = True
        assert (
            has_atom_key
        ), f"Neither 'atomic_numbers' nor 'pc_features' keys were present in data sample."
        return super().prologue(data)

    @staticmethod
    def get_atom_types(data: DataDict) -> torch.Tensor:
        """
        Extract out the atom types from a data sample to use as
        node data.

        If the ``atomic_numbers`` key is present in the data sample,
        this will be used. If this is absent but ``pc_features`` is
        available, we will infer the atom types from this instead.

        Parameters
        ----------
        data : DataDict
            Point cloud data sample to convert to a graph

        Returns
        -------
        torch.Tensor
            Source atom types from the point cloud

        Raises
        ------
        KeyError
            If neither ``atomic_numbers`` nor ``pc_features`` are
            available as keys.
        """
        if "atomic_numbers" in data:
            return data["atomic_numbers"]
        elif "pc_features" in data:
            (src_types, dst_types) = retrieve_pointcloud_node_types(data["pc_features"])
            assert src_types.size(0) == data["pos"].size(
                0
            ), f"Number of source nodes != number of atom positions!"
            return src_types
        else:
            raise KeyError(
                f"No suitable atom types to read from; expect either 'atomic_numbers' or 'pc_features' to read from a data sample."
            )

    @staticmethod
    def _apply_mask(
        atomic_numbers: torch.Tensor, pos: torch.Tensor, data: DataDict
    ) -> Tuple[torch.Tensor]:
        """
        Applies a mask to the data used to construct the graph.

        In some cases, like ``SyntheticPointGroupDataset``, the node
        data may come padded and a ``src_mask`` can be used to retrieve
        only non-padding nodes. If this key isn't present, then we will
        use the fact that ``atomic_numbers`` should be greater than zero
        to generate a mask to the data.

        Parameters
        ----------
        atomic_numbers : torch.Tensor
            Atomic numbers, as a 1D tensor [N,]
        pos : torch.Tensor
            Atomic positions, shape [N, 3]
        data : DataDict
            Point cloud data sample to transform into a graph

        Returns
        -------
        Tuple[torch.Tensor]
            Pair of atomic number and positions tensors as a 2-tuple.
        """
        if "src_mask" in data:
            mask = data.get("src_mask")
        else:
            mask = atomic_numbers > 0.0
        return (atomic_numbers[mask], pos[mask])

    @staticmethod
    def node_distances(coords: torch.Tensor) -> torch.Tensor:
        assert coords.ndim == 2, "Expected atom coordinates to be 2D tensor."
        assert coords.size(-1) == 3, "Expected XYZ coordinates."
        return torch.cdist(coords, coords, p=2).to(torch.float16)

    @staticmethod
    def edges_from_dist(
        dist_mat: Union[np.ndarray, torch.Tensor], cutoff: float
    ) -> List[List[int]]:
        if isinstance(dist_mat, np.ndarray):
            dist_mat = torch.from_numpy(dist_mat)
        lower_tri = torch.tril(dist_mat)
        # mask out self loops and atoms that are too far away
        mask = (0.0 < lower_tri) * (lower_tri < cutoff)
        adj_list = torch.argwhere(mask).tolist()
        return adj_list

    if package_registry["dgl"]:

        def _copy_node_keys_dgl(self, data: DataDict, graph: DGLGraph) -> None:
            # DGL variant of node data copying
            for key in self.node_keys:
                try:
                    graph.ndata[key] = data[key]
                except KeyError:
                    warn(
                        f"Expected node data '{key}' but was not found in data sample: {list(data.keys())}"
                    )

        def _copy_edge_keys_dgl(self, data: DataDict, graph: DGLGraph) -> None:
            # DGL variant of edge data copying
            edge_keys = getattr(self, "edge_keys", None)
            if edge_keys:
                for key in edge_keys:
                    try:
                        graph.edata[key] = data[key]
                    except KeyError:
                        warn(
                            f"Expected edge data {key} but was not found in data sample: {list(data.keys())}"
                        )

        def _convert_dgl(self, data: DataDict) -> None:
            atom_numbers = self.get_atom_types(data)
            coords = data["pos"]
            atom_numbers, coords = self._apply_mask(atom_numbers, coords, data)
            num_nodes = len(atom_numbers)
            # skip edge calculation if the distance matrix
            # exists already
            if "distance_matrix" not in data:
                dist_mat = self.node_distances(coords)
            else:
                dist_mat = data.get("distance_matrix")
            adj_list = self.edges_from_dist(dist_mat, self.cutoff_dist)
            g = dgl_graph(adj_list, num_nodes=num_nodes)
            g.ndata["atomic_numbers"] = atom_numbers
            g.ndata["pos"] = coords
            data["graph"] = g

    if package_registry["pyg"]:

        def _copy_node_keys_pyg(self, data: DataDict, graph: PyGGraph) -> None:
            for key in self.node_keys:
                try:
                    setattr(graph, key, data[key])
                except KeyError:
                    warn(
                        f"Expected node data '{key}' but was not found in data sample: {list(data.keys())}"
                    )

        def _copy_edge_keys_pyg(self, data: DataDict, graph: PyGGraph) -> None:
            edge_keys = getattr(self, "edge_keys", None)
            if edge_keys:
                for key in edge_keys:
                    try:
                        setattr(graph, key, data[key])
                    except KeyError:
                        warn(
                            f"Expected edge data '{key}' but was not found in data sample: {list(data.keys())}"
                        )

        def _convert_pyg(self, data: DataDict) -> None:
            """
            Structure data into a PyG format, which at the minimum, packs the
            atomic numbers and nuclear coordinates. Mutates the dictionary
            of data inplace.

            Parameters
            ----------
            data : DataDict
                Data structure read from base class
            """
            atom_numbers = self.get_atom_types(data)
            coords = data["pos"]
            atom_numbers, coords = self._apply_mask(atom_numbers, coords, data)
            if "distance_matrix" not in data:
                dist_mat = self.node_distances(coords)
            else:
                dist_mat = data.get("distance_matrix")
            # convert ensure edges are in the right format for PyG
            edge_index = torch.LongTensor(
                self.edges_from_dist(dist_mat, self.cutoff_dist)
            )
            # if not in the expected shape, transpose and reformat layout
            if edge_index.size(0) != 2 and edge_index.size(1) == 2:
                edge_index = edge_index.T.contiguous()
            g = PyGGraph(edge_index=edge_index, pos=coords)
            g.atomic_numbers = atom_numbers
            data["graph"] = g

    def convert(self, data: DataDict) -> None:
        if self.backend == "dgl":
            self._convert_dgl(data)
        else:
            self._convert_pyg(data)

    def copy_node_keys(self, data: DataDict, graph: AbstractGraph) -> None:
        if self.backend == "dgl":
            self._copy_node_keys_dgl(data, graph)
        else:
            self._copy_node_keys_pyg(data, graph)

    def copy_edge_keys(self, data: DataDict, graph: AbstractGraph) -> None:
        if self.backend == "dgl":
            self._copy_edge_keys_dgl(data, graph)
        else:
            self._copy_edge_keys_pyg(data, graph)

    def epilogue(self, data: DataDict) -> None:
        g = data["graph"]
        # copy over data to graph structure
        self.copy_node_keys(data, g)
        self.copy_edge_keys(data, g)
        # remove unused/redundant keys in DataDict
        for key in [
            "pos",
            "pc_features",
            "distance_matrix",
            "atomic_numbers",
            "src_nodes",
            "dst_nodes",
            "sizes",
        ]:
            try:
                del data[key]
            except KeyError:
                pass
        if self.backend == "dgl":
            # DGL graphs are inherently directed, so we need to add non-redundant
            # reverse edges to avoid issues with message passing
            data["graph"] = dgl.to_bidirected(data["graph"], copy_ndata=True)
        return super().epilogue(data)
