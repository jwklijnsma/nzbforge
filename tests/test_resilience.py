"""Dropped idle connections, timeouts and cross-group pipelining (the 'EOFError: connection closed' runs)."""
import time

from app import scanner
from app.db import SessionLocal
from app.models import Collection, Group


def overview(a, b, name="Pipe.Show.S01E01"):
    return [f'{n}\t"{name}.720p.HDTV.x264-GRP.part{n % 5 + 1:02d}.rar" yEnc ({n % 20 + 1}/20)\t'
            f"p@x\tMon, 23 Nov 2026 20:00:00 +0000\t<{n}@x>\t\t1000\t10" for n in range(a, b + 1)]


class Fake:
    made = []

    def __init__(self, *a, compress=None, **k):
        self.compressed = bool(compress)
        self.dead = False
        self.group_name = None
        Fake.made.append(self)

    def ping(self):
        if self.dead:
            raise EOFError("connection closed")

    def group(self, name):
        if self.dead:
            raise EOFError("connection closed")
        self.group_name = name
        return 1, 100

    def xover(self, a, b):
        if self.dead:
            raise EOFError("connection closed")
        return overview(a, b)

    def close(self):
        pass


def test_dead_idle_connections_are_replaced(monkeypatch):
    Fake.made = []
    monkeypatch.setattr(scanner, "NNTP", Fake)
    monkeypatch.setattr(scanner, "IDLE_CHECK", 0)
    pool = scanner.Pool(3)
    try:
        futs = [pool.xover("alt.binaries.x", i, i + 4) for i in range(1, 30, 5)]
        assert all(len(f.result()) == 5 for f in futs)
        for c in list(Fake.made):  # provider drops every idle connection (e.g. during finalize)
            c.dead = True
        before = len(Fake.made)
        futs = [pool.xover("alt.binaries.x", i, i + 4) for i in range(1, 30, 5)]
        assert all(len(f.result()) == 5 for f in futs)  # no EOFError reaches the caller
        assert len(Fake.made) > before
    finally:
        pool.close()


def test_dropped_connection_keeps_compression_on(monkeypatch):
    calls = []

    class Drop(Fake):
        def xover(self, a, b):
            calls.append(1)
            if len(calls) == 1:
                raise EOFError("connection closed inside compressed XOVER")
            return super().xover(a, b)

    Fake.made = []
    monkeypatch.setattr(scanner, "NNTP", Drop)
    pool = scanner.Pool(1)
    pool.compress = True
    try:
        assert len(pool.xover("alt.binaries.x", 1, 5).result()) == 5
        assert pool.compress is True
    finally:
        pool.close()


def test_timed_out_range_is_fetched_in_halves(client, monkeypatch):
    class Slow(Fake):
        def group(self, name):
            super().group(name)
            return 1, 800

        def xover(self, a, b):
            if b - a + 1 > 250:  # provider can't answer big ranges in time
                raise TimeoutError("The read operation timed out")
            return super().xover(a, b)

    Fake.made = []
    monkeypatch.setattr(scanner, "NNTP", Slow)
    monkeypatch.setattr(scanner, "BATCH", 800)
    monkeypatch.setattr(scanner, "INITIAL", 1000)
    with SessionLocal() as db:
        g = Group(name="alt.binaries.slowrange")
        db.add(g)
        db.commit()
        pool = scanner.Pool(2)
        try:
            assert scanner.scan_group(pool, db, g) == 800
        finally:
            pool.close()
        assert g.last_article == 800 and g.last_error is None
        db.query(Collection).filter_by(group_id=g.id).delete()
        db.commit()


def test_small_groups_are_pulled_in_parallel(client, monkeypatch):
    class Latent(Fake):
        def group(self, name):
            time.sleep(0.03)
            self.group_name = name
            return 1, 10

        def xover(self, a, b):
            time.sleep(0.03)
            return overview(a, b, self.group_name.replace(".", "_"))

    Fake.made = []
    monkeypatch.setattr(scanner, "NNTP", Latent)
    with SessionLocal() as db:
        groups = [Group(name=f"alt.binaries.small{i:02d}") for i in range(24)]
        db.add_all(groups)
        db.commit()
        done = []
        pool = scanner.Pool(8)
        t = time.monotonic()
        try:
            total = scanner.scan_groups(pool, db, groups, done=lambda i, g, n, e: done.append((i, n, e)))
        finally:
            pool.close()
        took = time.monotonic() - t
        assert total == 240 and [d[0] for d in done] == list(range(24)) and all(d[2] is None for d in done)
        assert all(g.last_article == 10 for g in groups)
        assert took < 24 * 0.06 / 3, took  # one group after the other would take >= 1.4 s
        for g in groups:
            db.query(Collection).filter_by(group_id=g.id).delete()
        db.commit()


def test_ingest_bulk_new_and_existing_collections(client):
    with SessionLocal() as db:
        g = Group(name="alt.binaries.bulk")
        db.add(g)
        db.commit()
        scanner.ingest(db, g, overview(1, 10, "Bulk.A") + overview(1, 3, "Bulk.B"))
        scanner.ingest(db, g, overview(11, 20, "Bulk.A"))  # same collection, next batch
        cols = {c.base: c for c in db.query(Collection).filter_by(group_id=g.id)}
        assert len(cols) == 2
        a = next(c for b, c in cols.items() if b.startswith("Bulk.A"))
        assert sum(len(f["parts"]) for f in a.data.values()) == 20
        db.query(Collection).filter_by(group_id=g.id).delete()
        db.commit()
