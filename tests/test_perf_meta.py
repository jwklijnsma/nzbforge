import random
import time

from app import downloader, enrich, scanner
from app.db import SessionLocal
from app.models import Collection, Group, Release


def _slow_yenc(lines):  # the previous per-byte implementation, as reference
    out, in_data = bytearray(), False
    for line in lines:
        if line.startswith(b"=ybegin"):
            in_data = True
            continue
        if line.startswith((b"=ypart", b"=yend")):
            if line.startswith(b"=yend"):
                break
            continue
        if not in_data:
            continue
        esc = False
        for b in line:
            if esc:
                out.append((b - 64 - 42) & 255)
                esc = False
            elif b == 61:
                esc = True
            else:
                out.append((b - 42) & 255)
    return bytes(out)


def _yenc_encode(data):
    out = bytearray()
    for b in data:
        e = (b + 42) & 255
        if e in (0, 10, 13, 61):
            out += bytes([61, (e + 64) & 255])
        else:
            out.append(e)
    return bytes(out)


def test_fast_yenc_matches_reference():
    data = bytes(random.Random(1).randrange(256) for _ in range(50_000))
    enc = _yenc_encode(data)
    lines = [b"=ybegin line=128 size=50000 name=x.nfo"] + [enc[i:i + 128] for i in range(0, len(enc), 128)]
    # a line must not end in a lone "=": move the escaped byte onto the same line, as encoders do
    fixed, carry = [], b""
    for ln in lines[1:]:
        ln = carry + ln
        carry = b""
        if ln.endswith(b"=") and not ln.endswith(b"=="):
            ln, carry = ln[:-1], b"="
        fixed.append(ln)
    lines = lines[:1] + fixed + [b"=yend size=50000"]
    assert scanner.yenc_decode(lines) == _slow_yenc(lines) == data


def test_finalize_skips_collections_still_being_posted(client):
    now = scanner.datetime.utcnow()
    with SessionLocal() as db:
        g = Group(name="alt.binaries.final")
        db.add(g)
        db.commit()
        part = {"total": 1, "parts": {"1": ["<a@x>", 10]}}
        db.add(Collection(group_id=g.id, poster="p", base="Done.Movie.2020.1080p", set_total=1,
                          posted=now - scanner.timedelta(hours=1), updated=now - scanner.timedelta(hours=1),
                          data={"Done.Movie.2020.1080p.mkv": part}))
        db.add(Collection(group_id=g.id, poster="p", base="Busy.Movie.2020.1080p", set_total=1,
                          posted=now, updated=now, data={"Busy.Movie.2020.1080p.mkv": part}))
        db.commit()
        assert scanner.finalize(db) == 1
        assert [c.base for c in db.query(Collection).filter_by(group_id=g.id)] == ["Busy.Movie.2020.1080p"]


def test_cinemeta_movie_without_key(monkeypatch):
    calls = []

    def fake_get(url):
        calls.append(url)
        if "/catalog/movie/" in url:
            return {"metas": [{"id": "tt0000001", "name": "Heat", "releaseInfo": "1986"},
                              {"id": "tt0113277", "name": "Heat", "releaseInfo": "1995"}]}
        assert url.endswith("/meta/movie/tt0113277.json")
        return {"meta": {"id": "tt0113277", "imdb_id": "tt0113277", "name": "Heat", "year": "1995",
                         "description": "<p>Cops &amp; robbers.</p>", "imdbRating": "8.3",
                         "genres": ["Action", "Crime"], "moviedb_id": 949, "poster": "https://img/p.jpg"}}

    monkeypatch.setattr(enrich, "TMDB_KEY", "")
    monkeypatch.setattr(enrich, "_get", fake_get)
    info = enrich._lookup(("movie", "Heat", 1995))
    assert info["imdbid"] == "tt0113277" and info["tmdbid"] == 949 and info["year"] == 1995
    assert info["overview"] == "Cops & robbers." and info["rating"] == 8.3 and info["genres"] == "Action, Crime"
    assert len(calls) == 2


def test_cinemeta_rejects_wrong_year(monkeypatch):
    monkeypatch.setattr(enrich, "TMDB_KEY", "")
    monkeypatch.setattr(enrich, "_get", lambda url: {"metas": [{"id": "tt1", "name": "Heat", "releaseInfo": "1986"}]})
    assert enrich._lookup(("movie", "Heat", 2024)) is None


def test_series_falls_back_to_cinemeta(monkeypatch):
    monkeypatch.setattr(enrich, "tvmaze", lambda t, y=None: None)
    monkeypatch.setattr(enrich, "cinemeta", lambda kind, t, y=None: {"meta_title": "Obscure Show", "genres": "Drama"})
    assert enrich._lookup(("tv", "Obscure Show", None))["meta_title"] == "Obscure Show"


def test_movies_enriched_without_tmdb_key(client, monkeypatch):
    monkeypatch.setattr(enrich, "TMDB_KEY", "")
    monkeypatch.setattr(enrich, "cinemeta", lambda kind, t, y=None: {"meta_title": t, "genres": "Drama", "imdbid": "tt9"})
    enrich._cache.clear()
    with SessionLocal() as db:
        db.add(Release(guid="m1", name="Free.Film.2021.1080p", searchname="free film", group_name="g", poster="p",
                       posted=scanner.datetime.utcnow(), size=1, category=2040, files_data=[], title="Free Film",
                       year=2021))
        db.commit()
        enrich.enrich_pending(db)
        r = db.query(Release).filter_by(guid="m1").one()
        assert r.imdbid == "tt9" and r.enriched_at is not None


def test_downloader_priority(monkeypatch):
    sent = {}

    def fake_open(req):
        sent["body"] = req.data
        return {"result": 1}

    monkeypatch.setattr(downloader, "KIND", "nzbget")
    monkeypatch.setattr(downloader, "URL", "http://nzbget")
    monkeypatch.setattr(downloader, "PRIORITY", "force")
    monkeypatch.setattr(downloader, "_open", fake_open)
    downloader.send("<nzb/>", "x", 2040)
    import json
    assert json.loads(sent["body"])["params"][3] == 900
