import io
import time

from conftest import ADMIN_KEY

from app import scanner
from app.db import SessionLocal
from app.models import Group

RealNNTP = scanner.NNTP

ACTIVE = [
    "alt.binaries.teevee 5000 1000 y",
    "alt.binaries.moovee 900000 1 y",
    "alt.binaries.dead 0 1 y",           # empty
    "alt.binaries.alias 10 1 =alt.binaries.teevee",
    "alt.binaries.gone 50 1 x",
    "alt.test 100 1 y",                   # doesn't match pattern
]


class FakeSock:
    def __init__(self):
        self.sent = []

    def sendall(self, b):
        self.sent.append(b.decode().strip())


def fake_nntp(lines, list_code=215):
    from conftest import fake_conn
    body = f"{list_code} list follows\r\n" + "".join(l + "\r\n" for l in lines) + ".\r\n"
    return fake_conn(body.encode())


def test_list_active_parses_and_filters():
    n = fake_nntp(ACTIVE)
    got = n.list_active("alt.binaries.*")
    assert n.sock.sent == ["LIST ACTIVE alt.binaries.*"]
    assert ("alt.binaries.teevee", 1000, 5000, "y") in got
    assert all(g[0].startswith("alt.binaries.") for g in got)


def test_import_groups_skips_empty_alias_and_removed(client):
    with SessionLocal() as db:
        added, matched = scanner.import_groups(db, fake_nntp(ACTIVE), "alt.binaries.*", 1)
        assert (added, matched) == (2, 2)
        names = set(db.scalars(Group.__table__.select().with_only_columns(Group.name)))
        assert {"alt.binaries.teevee", "alt.binaries.moovee"} <= names
        assert "alt.binaries.dead" not in names
        # second run adds nothing new; min_articles filters small groups
        assert scanner.import_groups(db, fake_nntp(ACTIVE), "alt.binaries.*", 10000) == (0, 1)


def test_import_endpoint(client, monkeypatch):
    h = {"X-Api-Key": ADMIN_KEY}
    monkeypatch.setattr(scanner, "HOST", "")
    r = client.post("/web/groups/import", json={}, headers=h)
    assert r.status_code == 400 and "NNTP_HOST" in r.json()["detail"]

    monkeypatch.setattr(scanner, "HOST", "news.example.com")
    monkeypatch.setattr(scanner, "NNTP", lambda *a, **k: fake_nntp(["alt.binaries.new.one 99 1 y"]))
    r = client.post("/web/groups/import", json={"pattern": "alt.binaries.new.*"}, headers=h)
    assert r.status_code == 202, r.text
    for _ in range(50):
        job = client.get("/web/jobs", headers=h).json()["import"]
        if job["state"] != "running":
            break
        time.sleep(0.05)
    assert (job["state"], job["matched"], job["added"]) == ("done", 1, 1), job["error"]


def test_bad_group_does_not_stop_pass(monkeypatch):
    scanned = []

    def scan_group(nntp, db, g):
        scanned.append(g.name)
        if len(scanned) == 1:
            raise RuntimeError("GROUP x: 411 no such group")
        return 5

    class N:
        def close(self):
            pass

    monkeypatch.setattr(scanner, "NNTP", lambda *a, **k: N())
    monkeypatch.setattr(scanner, "scan_group", scan_group)
    for f in ("finalize", "enrich_pending"):
        monkeypatch.setattr(scanner, f, lambda db: None)
    monkeypatch.setattr(scanner, "fetch_nfos", lambda nntp, db: None)
    scanner.scan_pass()
    assert len(scanned) >= 2
    last = scanner.status()["scanner"]["last_pass"]
    assert last["errors"] and "411" in last["errors"][0] and last["headers"] == 5 * (len(scanned) - 1)
    assert scanner.status()["scanner"]["state"] == "idle"
