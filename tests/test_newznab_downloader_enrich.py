"""Newznab XML + search filters, the NZBGet/SABnzbd sender, and the metadata
lookups (TVmaze, TMDB, Cinemeta) with the network faked."""
import base64
import io
import json
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import downloader, enrich, newznab
from app.db import Base
from app import models  # noqa: F401  (registers tables)
from app.models import Release

NS = {"newznab": "http://www.newznab.com/DTD/2010/feeds/attributes/"}


# --------------------------------------------------------------------------- newznab

@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(engine)()
    yield s
    s.close()


def rel(db, guid, name, **kw):
    defaults = dict(searchname=name.lower().replace(".", " "), group_name="alt.binaries.test", poster="p@x",
                    posted=datetime.utcnow() - timedelta(days=kw.pop("age", 1)), size=1000, category=5040,
                    files_data=[])
    r = Release(guid=guid, name=name, **{**defaults, **kw})
    db.add(r)
    db.commit()
    return r


def test_cat_names():
    assert newznab.cat_name(5000) == "TV"
    assert newznab.cat_name(5040) == "TV > HD"
    assert newznab.cat_name(5099) == "TV > 5099"
    assert newznab.cat_name(9000) == "Other"


def test_error_and_caps_are_valid_xml():
    err = ET.fromstring(newznab.error_xml(100, 'Incorrect "user" & key'))
    assert err.get("code") == "100" and err.get("description") == 'Incorrect "user" & key'
    caps = ET.fromstring(newznab.caps_xml("https://nzb.example/?a=1&b=2"))
    assert caps.find("server").get("url") == "https://nzb.example/?a=1&b=2"
    assert caps.find("searching/tv-search").get("supportedParams") == "q,tvdbid,imdbid,season,ep"
    assert {c.get("id") for c in caps.find("categories")} == {str(k) for k in newznab.CATS}
    assert caps.find("categories/category[@id='2000']/subcat[@id='2045']").get("name") == "UHD"


def test_search_filters(db):
    rel(db, "a", "Show.S01E02.720p", season=1, episode=2, category=5040)
    rel(db, "b", "Show.S01E03.720p", season=1, episode=3, category=5030)
    rel(db, "c", "Film.2024.1080p", category=2040, age=30)
    rel(db, "d", "Other_Show.S02E01", season=2, episode=1, category=5000)
    ids = lambda **kw: sorted(r.guid for r in newznab.search(db, **kw)[0])  # noqa: E731
    assert ids(q="show") == ["a", "b", "d"]
    assert ids(q="show 720p") == ["a", "b"]
    assert ids(cat="5000") == ["a", "b", "d"]                   # parent covers its subcats
    assert ids(cat="5030,2040") == ["b", "c"]
    assert ids(cat="junk") == ["a", "b", "c", "d"]
    assert ids(season="1", ep="3") == ["b"]
    assert ids(maxage="7") == ["a", "b", "d"]
    assert ids(q="film%") == ["c"]                              # % and _ become separators, not wildcards
    assert ids(q="%_") == ["a", "b", "c", "d"]
    rows, total = newznab.search(db, limit=2, offset=1)
    assert total == 4 and len(rows) == 2


def test_id_search_also_finds_releases_not_enriched_yet(db):
    rel(db, "known", "Show.S01E01", title="Show", tvdbid=123, imdbid="tt0012345", tmdbid=77)
    rel(db, "pending", "Show.S01E02", searchname="show s01e02")            # same title, no ids yet
    rel(db, "other", "Different.S01E01", tvdbid=999)
    ids = lambda **kw: sorted(r.guid for r in newznab.search(db, **kw)[0])  # noqa: E731
    assert ids(tvdbid="123") == ["known", "pending"]
    assert ids(imdbid="12345") == ["known", "pending"]            # zero-padded to tt0012345
    assert ids(imdbid="tt0012345") == ["known", "pending"]
    assert ids(tmdbid="77") == ["known", "pending"]
    assert ids(tvdbid="555") == []
    assert ids(tvdbid="abc") == ["known", "other", "pending"]     # non-numeric ids are ignored


def test_rss_items_have_newznab_attributes(db):
    r = rel(db, "g1", "Show & Co.S01E02", season=1, episode=2, imdbid="tt0012345", tvdbid=123, tmdbid=77,
            poster_url="https://img/x.jpg", rating=8.1, year=2024, overview="An <b>overview</b>")
    rel(db, "g2", "Plain.Movie", category=2000)
    rows, total = newznab.search(db)
    rss = ET.fromstring(newznab.rss_xml(rows, total, 0, "https://nzb.example", "KEY&1"))
    resp = rss.find("channel/newznab:response", NS)
    assert (resp.get("offset"), resp.get("total")) == ("0", "2")
    item = next(i for i in rss.iter("item") if i.find("guid").text == "g1")
    assert item.find("title").text == "Show & Co.S01E02"
    assert item.find("link").text == "https://nzb.example/api?t=get&id=g1&apikey=KEY&1"
    assert item.find("category").text == "TV > HD" and item.find("description").text == "An <b>overview</b>"
    assert item.find("enclosure").get("type") == "application/x-nzb"
    attrs = {(a.get("name"), a.get("value")) for a in item.findall("newznab:attr", NS)}
    assert {("category", "5000"), ("category", "5040"), ("season", "S01"), ("episode", "E02"),
            ("imdb", "0012345"), ("tvdbid", "123"), ("tmdbid", "77"), ("coverurl", "https://img/x.jpg"),
            ("rating", "8.1"), ("year", "2024")} <= attrs
    movie = next(i for i in rss.iter("item") if i.find("guid").text == "g2")
    names = [a.get("name") for a in movie.findall("newznab:attr", NS)]
    assert names.count("category") == 1 and "season" not in names


# --------------------------------------------------------------------------- downloader

@pytest.fixture
def dl(monkeypatch):
    sent = {}

    def configure(kind, **env):
        monkeypatch.setattr(downloader, "KIND", kind)
        monkeypatch.setattr(downloader, "URL", "http://dl:6789")
        for k, v in env.items():
            monkeypatch.setattr(downloader, k, v)

    def fake_open(req):
        sent["url"], sent["data"], sent["headers"] = req.full_url, req.data, dict(req.header_items())
        return sent.get("reply", {})

    monkeypatch.setattr(downloader, "_open", fake_open)
    monkeypatch.delenv("DOWNLOADER_CATEGORIES", raising=False)
    sent["configure"] = configure
    return sent


def test_no_downloader_configured(monkeypatch):
    monkeypatch.setattr(downloader, "URL", "")
    assert downloader.name() is None
    with pytest.raises(RuntimeError, match="No downloader configured"):
        downloader.send("<nzb/>", "t", 2000)


def test_categories(dl, monkeypatch):
    dl["configure"]("sabnzbd")
    assert downloader.category_for(5040) == "tv" and downloader.category_for(7000) == ""
    monkeypatch.setenv("DOWNLOADER_CATEGORIES", "2000=Films,2045=4K,bad")
    assert downloader.category_for(2045) == "4K" and downloader.category_for(2040) == "Films"


def test_nzbget_append(dl):
    dl["configure"]("nzbget", USER="u", PASS="p", PRIORITY="force")
    dl["reply"] = {"result": 42}
    downloader.send("<nzb/>", "My.Show.S01E01", 5040)
    assert downloader.name() == "NZBGet" and dl["url"] == "http://dl:6789/jsonrpc"
    body = json.loads(dl["data"])
    assert body["method"] == "append"
    name, content, category, priority = body["params"][:4]
    assert (name, base64.b64decode(content), category, priority) == ("My.Show.S01E01.nzb", b"<nzb/>", "Series", 900)
    assert dl["headers"]["Authorization"] == "Basic " + base64.b64encode(b"u:p").decode()


def test_nzbget_refusal(dl):
    dl["configure"]("nzbget", USER="", PRIORITY="bogus")
    dl["reply"] = {"result": 0, "error": "bad nzb"}
    with pytest.raises(RuntimeError, match="NZBGet refused the NZB: bad nzb"):
        downloader.send("<nzb/>", "t", 2000)
    assert "Authorization" not in dl["headers"] and json.loads(dl["data"])["params"][3] == 0


def test_sabnzbd_addfile(dl):
    dl["configure"]("sabnzbd", APIKEY="k3y", PRIORITY="high")
    dl["reply"] = {"status": True}
    downloader.send("<nzb>x</nzb>", "Film 2024", 2040)
    assert downloader.name() == "SABnzbd"
    assert "mode=addfile" in dl["url"] and "apikey=k3y" in dl["url"] and "cat=movies" in dl["url"]
    assert "priority=1" in dl["url"] and "nzbname=Film+2024" in dl["url"]
    assert b'filename="Film 2024.nzb"' in dl["data"] and b"<nzb>x</nzb>" in dl["data"]
    assert dl["headers"]["Content-type"].startswith("multipart/form-data; boundary=")
    dl["reply"] = {"status": False, "error": "API Key Incorrect"}
    with pytest.raises(RuntimeError, match="API Key Incorrect"):
        downloader.send("<nzb/>", "x", 2040)


# --------------------------------------------------------------------------- enrich

class FakeHTTP:
    """urllib.request.urlopen replacement: url substring -> JSON, HTTPError code, or list of those."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.calls.append(url)
        for part, reply in self.routes.items():
            if part in url:
                if isinstance(reply, list):
                    reply = reply.pop(0)
                if isinstance(reply, int):
                    raise urllib.error.HTTPError(url, reply, "x", {"Retry-After": "0"}, None)
                return io.BytesIO(json.dumps(reply).encode())
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)


@pytest.fixture
def http(monkeypatch):
    def install(routes):
        fake = FakeHTTP(routes)
        monkeypatch.setattr(enrich.urllib.request, "urlopen", fake)
        monkeypatch.setattr(enrich.time, "sleep", lambda s: None)
        return fake
    return install


def test_rate_limiter_spaces_requests(monkeypatch):
    clock, sleeps = [100.0], []
    monkeypatch.setattr(enrich.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(enrich.time, "sleep", sleeps.append)
    r = enrich._Rate(2)
    for _ in range(3):
        r.wait()
    assert sleeps == [0.0, 0.5, 1.0]


def test_get_retries_once_on_429(http):
    fake = http({"api.tvmaze.com": [429, {"name": "ok"}]})
    assert enrich._get("https://api.tvmaze.com/x") == {"name": "ok"}
    assert len(fake.calls) == 2
    http({"api.tvmaze.com": [429, 429]})
    with pytest.raises(urllib.error.HTTPError):
        enrich._get("https://api.tvmaze.com/x")


def test_clean_strips_html():
    assert enrich._clean("<p>Tom &amp; Jerry</p>\n<b>again</b>") == "Tom & Jerry again"
    assert enrich._clean(None) == ""


def test_tvmaze(http):
    http({"singlesearch": {"name": "The Show", "premiered": "2019-04-01", "summary": "<p>Plot</p>",
                           "image": {"medium": "https://img"}, "rating": {"average": 7.9},
                           "externals": {"imdb": "tt1", "thetvdb": 5}, "genres": ["Drama", "Crime"],
                           "url": "https://tvmaze/show"}})
    assert enrich.tvmaze("the show") == {
        "meta_title": "The Show", "year": 2019, "overview": "Plot", "poster_url": "https://img",
        "rating": 7.9, "imdbid": "tt1", "tvdbid": 5, "genres": "Drama, Crime", "website": "https://tvmaze/show"}
    http({})
    assert enrich.tvmaze("unknown") is None


def test_tmdb_movie(http, monkeypatch):
    monkeypatch.setattr(enrich, "TMDB_KEY", "k")
    fake = http({"search/movie": {"results": [{"id": 9, "title": "Film", "release_date": "2024-01-01",
                                               "overview": "o", "poster_path": "/p.jpg", "vote_average": 7.456,
                                               "genre_ids": [28, 99999]}]},
                 "external_ids": {"imdb_id": "tt9"}})
    info = enrich.tmdb_movie("film", 2024)
    assert info["poster_url"] == "https://image.tmdb.org/t/p/w342/p.jpg" and info["rating"] == 7.5
    assert (info["imdbid"], info["tmdbid"], info["genres"]) == ("tt9", 9, "Action")
    assert "year=2024" in fake.calls[0]
    http({"search/movie": {"results": [{"id": 9, "title": "Film"}]}, "external_ids": 500})
    assert enrich.tmdb_movie("film")["imdbid"] is None                 # external ids are optional
    http({"search/movie": {"results": []}})
    assert enrich.tmdb_movie("nothing") is None


def test_cinemeta_picks_the_matching_year_and_refuses_wrong_films(http):
    http({"catalog/movie": {"metas": [{"id": "tt1", "name": "Dune", "releaseInfo": "1984"},
                                      {"id": "tt2", "name": "Dune", "releaseInfo": "2021"}]},
          "meta/movie/tt2": {"meta": {"name": "Dune", "year": "2021", "imdbRating": "8.0",
                                      "genres": ["Sci-Fi"], "moviedb_id": "438631"}}})
    info = enrich.cinemeta("movie", "Dune", 2021)
    assert (info["imdbid"], info["year"], info["rating"], info["tmdbid"]) == ("tt2", 2021, 8.0, 438631)
    assert info["website"] == "https://www.imdb.com/title/tt2/"
    http({"catalog/movie": {"metas": [{"id": "tt1", "name": "Dune", "releaseInfo": "1984"}]}})
    assert enrich.cinemeta("movie", "Dune", 2021) is None              # wrong film is worse than none
    http({"catalog/series": {"metas": []}})
    assert enrich.cinemeta("tv", "x") is None


def test_lookup_falls_through_sources(monkeypatch):
    monkeypatch.setattr(enrich, "CINEMETA", True)
    monkeypatch.setattr(enrich, "TMDB_KEY", "")

    def down(*a):
        raise OSError("tvmaze down")

    monkeypatch.setattr(enrich, "tvmaze", down)
    monkeypatch.setattr(enrich, "cinemeta", lambda kind, t, y: {"meta_title": f"{kind}:{t}"})
    assert enrich._lookup(("tv", "Show", None)) == {"meta_title": "tv:Show"}
    monkeypatch.setattr(enrich, "cinemeta", lambda kind, t, y: None)
    with pytest.raises(OSError, match="tvmaze down"):                  # all failed: retry next pass
        enrich._lookup(("tv", "Show", None))
    monkeypatch.setattr(enrich, "CINEMETA", False)
    assert enrich._lookup(("movie", "Film", 2024)) is None             # no movie source configured


def test_enrich_pending_reuses_known_metadata_and_caches(db, monkeypatch):
    enrich._cache.clear()
    monkeypatch.setattr(enrich, "CINEMETA", False)
    monkeypatch.setattr(enrich, "TMDB_KEY", "")
    rel(db, "old", "Show.S01E01", title="Show", category=5040, meta_title="The Show", genres="Drama",
        enriched_at=datetime.utcnow(), tvdbid=5)
    rel(db, "new", "Show.S01E02", title="Show", category=5040)
    rel(db, "other", "Other.S01E01", title="Other", category=5040)
    rel(db, "film", "Film.2024", title="Film", category=2040)          # no movie source -> not looked at
    lookups = []
    monkeypatch.setattr(enrich, "tvmaze", lambda t, y=None: lookups.append(t) or None)
    assert enrich.enrich_pending(db) == 1
    new = db.query(Release).filter_by(guid="new").one()
    assert (new.meta_title, new.tvdbid, new.genres) == ("The Show", 5, "Drama")
    other = db.query(Release).filter_by(guid="other").one()
    assert other.enriched_at is not None and other.genres == ""         # looked up, nothing known
    assert lookups == ["Other"]
    assert db.query(Release).filter_by(guid="film").one().enriched_at is None
    assert enrich.enrich_pending(db) == 0                              # nothing pending any more
