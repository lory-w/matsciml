from pathlib import Path

from ocpmodels.datasets.carolina_db.carolina_api import CMDRequest
from ocpmodels.datasets.carolina_db.dataset import CMDataset

carolinadb_devset = Path(__file__).parents[0].joinpath("devset")
