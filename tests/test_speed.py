import io
import threading
import time
import zlib

import pytest

from app import enrich, scanner
from app.db import SessionLocal
from app.models import Group, Release

RealNNTP = scanner.NNTP


def overview(a, b):
    return [f'{n}\t"Speed.Show.S01E01.720p.HDTV.x264-GRP.part{n % 5 + 1:02d}.rar" yEnc ({n % 20 + 1}/20)\t'
            f"p@x\tMon, 23 Nov 2026 20:00:00 +0000\t<{n}@x>\t\t1000\t10" for n in range(a, b + 1)]


class FakeSock:
    def sendall(self, b):
        pass


@pytest.mark.parametrize("wbits", [zlib.MAX_WBITS, 31])  # zlib and gzip
def test_compressed_xover(wbits):
    lines = overview(1, 300)
    payload = ("\r\n".join(lines) + "\r\n.\r\n").encode()
    c = zlib.compressobj(wbits=wbits)
    blob = c.compress(payload) + c.flush()
    n = RealNNTP.__new__(RealNNTP)
    n.sock = FakeSock()
    n.f = io.BufferedReader(io.BytesIO(b"224 compressed data follows\r\n" + blob + b".\r\n"))
    assert n.xover(1, 300) == lines
    assert n.f.read() == b""  # the trailing ".\r\n" was consumed too


def test_plain_xover_still_works():
    lines = overview(1, 3)
    n = RealNNTP.__new__(RealNNTP)
    n.sock = FakeSock()
    n.f = io.BufferedReader(io.BytesIO(("224 ok\r\n" + "\r\n".join(lines) + "\r\n.\r\n").encode()))
    assert n.xover(1, 3) == lines


class FakeNNTP:
    """Slow fake server connection: records which thread served which range."""
    made = []

    def __init__(self, *a):
        self.group_name = None
        self.compressed = False
        FakeNNTP.made.append(self)
        self.calls = []

    def group(self, name):
        self.group_name = name
        return 1, 10_000

    def xover(self, a, b):
        assert self.group_name == "alt.binaries.speed"
        time.sleep(0.05)
        self.calls.append((a, b, threading.current_thread().name))
        return overview(a, b)

    def close(self):
        pass


def test_parallel_pipelined_scan(client, monkeypatch):
    FakeNNTP.made = []
    monkeypatch.setattr(scanner, "NNTP", FakeNNTP)
    monkeypatch.setattr(scanner, "BATCH", 500)
    monkeypatch.setattr(scanner, "INITIAL", 10_000)
    with SessionLocal() as db:
        g = Group(name="alt.binaries.speed")
        db.add(g)
        db.commit()
        pool = scanner.Pool(4)
        t = time.monotonic()
        try:
            got = scanner.scan_group(pool, db, g)
        finally:
            pool.close()
        took = time.monotonic() - t
        assert got == 10_000 and g.last_article == 10_000
        assert len({c for c in FakeNNTP.made if c.calls}) == 4  # all four connections did work
        assert took < 20 * 0.05  # 20 batches of 50 ms would take >= 1 s on one connection


def test_pool_reconnects_after_drop(monkeypatch):
    class Flaky(FakeNNTP):
        def xover(self, a, b):
            if len(FakeNNTP.made) == 1:
                raise OSError("connection reset")
            return super().xover(a, b)

    FakeNNTP.made = []
    monkeypatch.setattr(scanner, "NNTP", Flaky)
    pool = scanner.Pool(1)
    try:
        assert len(pool.xover("alt.binaries.speed", 1, 5).result()) == 5
    finally:
        pool.close()
    assert len(FakeNNTP.made) == 2


def test_enrich_reuses_known_titles_and_caches(client, monkeypatch):
    calls = []

    def fake_tvmaze(title, year=None):
        calls.append(title)
        return {"meta_title": title.title(), "genres": "Drama", "overview": "x", "website": "https://tvmaze/1"}

    monkeypatch.setattr(enrich, "tvmaze", fake_tvmaze)
    enrich._cache.clear()
    with SessionLocal() as db:
        def add(guid, title):
            db.add(Release(guid=guid, name=guid, searchname=guid, group_name="g", poster="p",
                           posted=scanner.datetime.utcnow(), size=1, category=5040, files_data=[], title=title))
        add("e1", "Speedshow")
        add("e2", "Speedshow")
        db.commit()
        enrich.enrich_pending(db)
        assert calls == ["Speedshow"]  # two episodes, one web lookup
        add("e3", "Speedshow")  # a later episode, next pass
        db.commit()
        enrich._cache.clear()  # even without the in-memory cache the DB has it
        enrich.enrich_pending(db)
        assert calls == ["Speedshow"]
        r = db.scalar(scanner.select(Release).where(Release.guid == "e3"))
        assert r.meta_title == "Speedshow" and r.genres == "Drama"
