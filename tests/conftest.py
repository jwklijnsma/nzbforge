import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

ADMIN_KEY = "test-admin-key"


@pytest.fixture(scope="session")
def client(tmp_path_factory):
    db_file = tmp_path_factory.mktemp("db") / "test.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_file}"
    os.environ["ADMIN_KEY"] = ADMIN_KEY

    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:  # runs lifespan -> init_db + admin user
        yield c
