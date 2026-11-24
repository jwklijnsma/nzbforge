import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

ADMIN_KEY = "test-admin-key"
_REAL = {}

# Must be set before anything imports app.db (test modules import app.* at collection time).
_tmp = tempfile.mkdtemp(prefix="nzbforge-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["ADMIN_KEY"] = ADMIN_KEY
os.environ.pop("NNTP_HOST", None)
os.environ["SCANNER"] = "0"

from app import scanner as _scanner  # noqa: E402  (after the env above)

_REAL["NNTP"] = _scanner.NNTP


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:  # runs lifespan -> init_db + admin user
        yield c


class ByteSock:
    """Fake socket: recv() hands out the given bytes in small chunks, then blocks like a silent server."""

    def __init__(self, data: bytes, chunk=4096, silent_after=False):
        self.data, self.chunk, self.silent_after, self.sent = data, chunk, silent_after, []

    def recv(self, n):
        if not self.data:
            if self.silent_after:
                import socket
                raise socket.timeout("The read operation timed out")
            return b""
        out, self.data = self.data[:min(n, self.chunk)], self.data[min(n, self.chunk):]
        return out

    def sendall(self, b):
        self.sent.append(b.decode().strip())

    _quiet = None

    def fileno(self):  # a pipe that never becomes readable, for select()
        if ByteSock._quiet is None:
            import os
            ByteSock._quiet = os.pipe()
        return ByteSock._quiet[0]

    def pending(self):  # like ssl.SSLSocket.pending(): bytes readable without waiting
        return len(self.data)


def fake_conn(data: bytes, **kw):
    from app import scanner
    real = _REAL.setdefault("NNTP", scanner.NNTP if isinstance(scanner.NNTP, type) else None)
    n = real.__new__(real)
    n.sock = ByteSock(data, **kw)
    n.r = scanner.Reader(n.sock)
    return n
