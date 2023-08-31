import pytorch_lightning as pl
from torch.nn import LazyBatchNorm1d, SiLU

from matsciml.lightning.data_utils import MatSciMLDataModule
from matsciml.datasets.transforms import PointCloudToGraphTransform
from matsciml.models import PLEGNNBackbone
from matsciml.models.base import ScalarRegressionTask, BinaryClassificationTask

pl.seed_everything(21616)

model_args = {
    "embed_in_dim": 128,
    "embed_hidden_dim": 32,
    "embed_out_dim": 128,
    "embed_depth": 5,
    "embed_feat_dims": [128, 128, 128],
    "embed_message_dims": [128, 128, 128],
    "embed_position_dims": [64, 64],
    "embed_edge_attributes_dim": 0,
    "embed_activation": "relu",
    "embed_residual": True,
    "embed_normalize": True,
    "embed_tanh": True,
    "embed_activate_last": False,
    "embed_k_linears": 1,
    "embed_use_attention": False,
    "embed_attention_norm": "sigmoid",
    "readout": "sum",
    "node_projection_depth": 3,
    "node_projection_hidden_dim": 128,
    "node_projection_activation": "relu",
    "prediction_out_dim": 1,
    "prediction_depth": 3,
    "prediction_hidden_dim": 128,
    "prediction_activation": "relu",
    "encoder_only": True,
}

model = PLEGNNBackbone(**model_args)
task = ScalarRegressionTask(
    model,
    output_kwargs={"norm": LazyBatchNorm1d, "hidden_dim": 256, "activation": SiLU},
    lr=1e-3,
    task_keys=["efermi"],
)

dm = MatSciMLDataModule(
    dataset="MaterialsProjectDataset",
    train_path="mp_data/base",
    dset_kwargs={
        "transforms": [
            PointCloudToGraphTransform(
                "dgl", cutoff_dist=20.0, node_keys=["pos", "atomic_numbers"]
            )
        ]
    },
    val_split=0.2,
    batch_size=16,
    num_workers=2,
)

trainer = pl.Trainer(
    fast_dev_run=100,
    accelerator="cpu",
    devices=1,
)

trainer.fit(task, datamodule=dm)
