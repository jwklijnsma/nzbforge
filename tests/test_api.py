from conftest import ADMIN_KEY


def test_caps_needs_no_key(client):
    r = client.get("/api", params={"t": "caps"})
    assert r.status_code == 200
    assert "xml" in r.headers["content-type"]
    assert "<caps" in r.text


def test_api_rejects_bad_key(client):
    r = client.get("/api", params={"t": "search", "apikey": "wrong"})
    assert r.status_code == 200
    assert 'code="100"' in r.text


def test_search_with_admin_key(client):
    r = client.get("/api", params={"t": "search", "q": "anything", "apikey": ADMIN_KEY})
    assert r.status_code == 200
    assert "<rss" in r.text


def test_unknown_function(client):
    r = client.get("/api", params={"t": "nope", "apikey": ADMIN_KEY})
    assert 'code="202"' in r.text


def test_web_me(client):
    assert client.get("/web/me").status_code == 401
    r = client.get("/web/me", headers={"X-Api-Key": ADMIN_KEY})
    assert r.status_code == 200
    assert r.json() == {"username": "admin", "is_admin": True, "downloader": None}


def test_groups_and_stats(client):
    h = {"X-Api-Key": ADMIN_KEY}
    assert client.post("/web/groups", json={"name": "alt.binaries.test"}, headers=h).json() == {"ok": True}
    assert client.post("/web/groups", json={"name": "alt.binaries.test"}, headers=h).status_code == 400
    assert [g["name"] for g in client.get("/web/groups", headers=h).json()] == ["alt.binaries.test"]
    s = client.get("/web/stats", headers=h).json()
    assert s["groups"] == 1 and s["releases"] == 0


def test_group_toggle_delete_and_pull(client):
    h = {"X-Api-Key": ADMIN_KEY}
    client.post("/web/groups", json={"name": "alt.binaries.toggle"}, headers=h)
    r = client.patch("/web/groups/alt.binaries.toggle", json={"active": False}, headers=h)
    assert r.json()["active"] is False
    assert client.post("/web/pull", json={}, headers=h).status_code == 400  # NNTP_HOST not set in tests
    jobs = client.get("/web/jobs", headers=h).json()
    assert {"scanner", "import"} <= set(jobs)
    assert client.delete("/web/groups/alt.binaries.toggle", headers=h).json() == {"ok": True}
    assert client.delete("/web/groups/alt.binaries.toggle", headers=h).status_code == 404
