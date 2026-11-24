import io

from conftest import ByteSock, fake_conn
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


def _compressed(lines, wbits=zlib.MAX_WBITS, close=True):
    payload = ("\r\n".join(lines) + "\r\n.\r\n").encode()
    c = zlib.compressobj(wbits=wbits)
    return c.compress(payload) + (c.flush() if close else c.flush(zlib.Z_SYNC_FLUSH))


@pytest.mark.parametrize("wbits", [zlib.MAX_WBITS, 31])  # zlib and gzip
@pytest.mark.parametrize("chunk", [7, 4096])                # tiny reads: terminator split over reads
def test_compressed_xover_with_terminator(wbits, chunk):
    lines = overview(1, 300)
    n = fake_conn(b"224 compressed data follows\r\n" + _compressed(lines, wbits) + b".\r\n"
                  + b"211 next\r\n", chunk=chunk)
    assert n.xover(1, 300) == lines
    assert n._status() == (211, "next")  # positioned exactly after the terminator


def test_compressed_xover_without_terminator_does_not_hang():
    lines = overview(1, 50)
    # closed stream, no '.\\r\\n' after it, and the server goes quiet (would time out after 60 s before)
    n = fake_conn(b"224 ok\r\n" + _compressed(lines), silent_after=True)
    t = time.monotonic()
    assert n.xover(1, 50) == lines
    assert time.monotonic() - t < 1


def test_flushed_open_stream_is_complete():
    lines = overview(1, 50)
    n = fake_conn(b"224 ok\r\n" + _compressed(lines, close=False), silent_after=True)
    assert n.xover(1, 50) == lines


def test_plain_xover_and_empty_reply():
    lines = overview(1, 3)
    n = fake_conn(("224 ok\r\n" + "\r\n".join(lines) + "\r\n.\r\n224 ok\r\n.\r\n").encode(), chunk=5)
    assert n.xover(1, 3) == lines
    assert n.xover(4, 9) == []


def test_body_unescapes_dots():
    n = fake_conn(b"222 body\r\n=ybegin\r\n..dot line\r\nend\r\n.\r\n")
    assert n.body("<x@y>") == [b"=ybegin", b".dot line", b"end"]


def test_refused_greeting_raises(monkeypatch):
    class S(ByteSock):
        def close(self):
            pass

    monkeypatch.setattr(scanner.socket, "create_connection", lambda *a, **k: S(b"502 too many connections\r\n"))
    with pytest.raises(ConnectionRefusedError, match="502"):
        RealNNTP("h", 119, "", "", use_ssl=False)


class FakeNNTP:
    """Slow fake server connection: records which thread served which range."""
    made = []

    def __init__(self, *a, **k):
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
        assert len([c for c in FakeNNTP.made if c.calls]) == 4  # all four connections did work
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


def test_pool_continues_when_provider_refuses_more_connections(monkeypatch):
    class Limited(FakeNNTP):
        def __init__(self, *a, **k):
            if len(FakeNNTP.made) >= 2:
                raise ConnectionRefusedError("server refused connection: 502 too many connections")
            super().__init__(*a, **k)

    FakeNNTP.made = []
    monkeypatch.setattr(scanner, "NNTP", Limited)
    pool = scanner.Pool(6)
    try:
        futs = [pool.xover("alt.binaries.speed", x, x + 9) for x in range(1, 200, 10)]
        assert sum(len(f.result()) for f in futs) == 200
        assert pool.connections() == 2 and pool.limit == 2
    finally:
        pool.close()


def test_compression_switched_off_after_failure(monkeypatch):
    class Broken(FakeNNTP):
        def __init__(self, *a, compress=None, **k):
            super().__init__(*a, **k)
            self.compressed = bool(compress)

        def xover(self, a, b):
            if self.compressed:
                raise zlib.error("bad stream")
            return super().xover(a, b)

    FakeNNTP.made = []
    monkeypatch.setattr(scanner, "NNTP", Broken)
    pool = scanner.Pool(1)
    pool.compress = True
    try:
        assert len(pool.xover("alt.binaries.speed", 1, 5).result()) == 5
        assert pool.compress is False
    finally:
        pool.close()
