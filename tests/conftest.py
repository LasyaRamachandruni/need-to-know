from pathlib import Path

import pytest

from needtoknow.context import ContextService
from needtoknow.data.pipeline import build
from needtoknow.data.synth import generate


@pytest.fixture(scope="session")
def dataset():
    return generate(users=25, seed=7)


@pytest.fixture(scope="session")
def warehouse(dataset, tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("wh") / "warehouse.duckdb"
    build(dataset.events, path)
    return path


@pytest.fixture()
def service(warehouse):
    s = ContextService(warehouse)
    yield s
    s.close()
