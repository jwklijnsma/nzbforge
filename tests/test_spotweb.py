import json
from datetime import datetime

import pytest
from conftest import ADMIN_KEY

from app import downloader, scanner
from app.db import SessionLocal
from app.models import Release
from app.parse import categorize, release_info

H = {"X-Api-Key": ADMIN_KEY}


@pytest.mark.parametrize("name,expect", [
    ("Dune.Part.Two.2024.NL.Subs.2160p.WEB-DL.DDP5.1.Atmos.DV.HDR10.H.265-FLUX",
     {"resolution": "2160p", "source": "WEB-DL", "video_codec": "x265", "hdr": "Dolby Vision, HDR10",
      "audio": "DD+ Atmos", "subtitles": "Dutch", "languages": None, "release_group": "FLUX"}),
    ("Flikken.Maastricht.S18E01.DUTCH.720p.WEB.h264-NLKIDS",
     {"resolution": "720p", "languages": "Dutch", "video_codec": "x264", "release_group": "NLKIDS"}),
    ("Oppenheimer.2023.2160p.UHD.BluRay.REMUX.HDR10Plus.HEVC.TrueHD.7.1.Atmos-FGT",
     {"source": "Remux", "hdr": "HDR10+", "audio": "TrueHD Atmos"}),
    ("Some.Film.2020.EXTENDED.REPACK.1080p.BluRay.x264-GRP", {"edition": "Repack, Extended"}),
])
def test_release_info(name, expect):
    info = release_info(name)
    for k, v in expect.items():
        assert info[k] == v, k


@pytest.mark.parametrize("name,exts,cat", [
    ("Some.Game-TENOKE", (), 4050),
    ("Mario.Kart.World.NSW-VENOM", (), 1000),
    ("Adobe.Photoshop.2025.v26.1.0.x64.Multilingual", (), 4000),
    ("Author - Book Title", ("epub",), 7020),
    ("The.Show.S01E01.1080p.WEB-DL.x264-GRP", (), 5040),
    ("random stuff", (), 7000),
])
def test_categorize(name, exts, cat):
    assert categorize(name, exts)[0] == cat


def _add(db, name, cat, **kw):
    r = Release(guid=name[:40], name=name, searchname=name.lower(), group_name="alt.binaries.test", poster="me",
                posted=datetime.utcnow(), size=100, category=cat, files_data=[{"name": f"{name}.mkv", "total": 1,
                "parts": [[1, "a@b", 100]]}], title=name, **kw)
    db.add(r)
    return r


def test_backfill_categories_and_filters(client):
    with SessionLocal() as db:
        _add(db, "Spot.Film.2024.NL.Subs.2160p.WEB-DL.x265-AAA", 2045)
        _add(db, "Spot.Serie.S01E01.720p.HDTV.x264-BBB", 5040)
        _add(db, "Spot.Game-TENOKE", 7000)  # indexed before games existed
        db.commit()
        scanner.tag_pending(db)
        assert db.scalar(Release.__table__.select().with_only_columns(Release.category)
                         .where(Release.name == "Spot.Game-TENOKE")) == 4050

    r = client.get("/web/search", params={"q": "spot", "resolution": "2160p"}, headers=H).json()
    assert [i["name"] for i in r["items"]] == ["Spot.Film.2024.NL.Subs.2160p.WEB-DL.x265-AAA"]
    assert r["items"][0]["subtitles"] == "Dutch"
    assert client.get("/web/search", params={"q": "spot", "lang": "dutch"}, headers=H).json()["total"] == 1
    assert client.get("/web/search", params={"q": "spot", "source": "HDTV"}, headers=H).json()["total"] == 1
    names = [i["name"] for i in client.get("/web/search", params={"q": "spot", "sort": "title", "order": "asc"},
                                           headers=H).json()["items"]]
    assert names == sorted(names, key=str.lower)

    cats = client.get("/web/categories", headers=H).json()
    pc = next(c for c in cats["categories"] if c["id"] == 4000)
    assert pc["count"] >= 1 and next(s for s in pc["subs"] if s["id"] == 4050)["count"] >= 1


def test_send_to_nzbget(client, monkeypatch):
    guid = client.get("/web/search", params={"q": "spot serie"}, headers=H).json()["items"][0]["guid"]
    assert client.post(f"/web/release/{guid}/send", headers=H).status_code == 502  # not configured

    sent = {}

    def fake_open(req):
        sent["url"] = req.full_url
        sent["body"] = json.loads(req.data)
        return {"result": 42}

    monkeypatch.setattr(downloader, "KIND", "nzbget")
    monkeypatch.setattr(downloader, "URL", "http://nzbget:6789")
    monkeypatch.setattr(downloader, "_open", fake_open)
    r = client.post(f"/web/release/{guid}/send", headers=H)
    assert r.json() == {"ok": True, "downloader": "NZBGet"}
    assert sent["url"] == "http://nzbget:6789/jsonrpc"
    assert sent["body"]["method"] == "append" and sent["body"]["params"][2] == "Series"
