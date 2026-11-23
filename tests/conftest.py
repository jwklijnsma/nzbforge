import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

ADMIN_KEY = "test-admin-key"

# Must be set before anything imports app.db (test modules import app.* at collection time).
_tmp = tempfile.mkdtemp(prefix="nzbforge-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["ADMIN_KEY"] = ADMIN_KEY
os.environ.pop("NNTP_HOST", None)
os.environ["SCANNER"] = "0"


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:  # runs lifespan -> init_db + admin user
        yield c
