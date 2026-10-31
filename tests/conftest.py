import sys
from pathlib import Path

import pytest
from rest_framework.test import APIClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant import runtime  # noqa: E402
from assistant.database import build_database, get_readonly_connection  # noqa: E402

CSV = Path(__file__).resolve().parent.parent / "data" / "sales.csv"


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "test.db"
    build_database(CSV, path)
    return path


@pytest.fixture()
def conn(db_path):
    c = get_readonly_connection(db_path)
    yield c
    c.close()


@pytest.fixture()
def catalog(conn):
    from assistant.analytics import load_catalog
    return load_catalog(conn)


@pytest.fixture()
def client(tmp_path):
    """API client running WITHOUT an LLM (deterministic rule-based interpreter)."""
    runtime.initialize(csv_path=CSV, db_path=tmp_path / "api.db", provider=None)
    return APIClient()
