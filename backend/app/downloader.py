"""Send an NZB to NZBGet or SABnzbd (Spotweb's 'send to downloader').

DOWNLOADER=nzbget|sabnzbd, DOWNLOADER_URL=http://host:port, DOWNLOADER_USER / DOWNLOADER_PASS (NZBGet),
DOWNLOADER_APIKEY (SABnzbd), DOWNLOADER_CATEGORIES="2000=Movies,5000=Series,3000=Music" (Newznab parent -> category),
DOWNLOADER_PRIORITY=normal|high|force (force starts it right away, even when the queue is paused).
"""
import base64
import json
import os
import urllib.parse
import urllib.request
import uuid

KIND = os.getenv("DOWNLOADER", "").strip().lower()
URL = os.getenv("DOWNLOADER_URL", "").rstrip("/")
USER = os.getenv("DOWNLOADER_USER", "")
PASS = os.getenv("DOWNLOADER_PASS", "")
APIKEY = os.getenv("DOWNLOADER_APIKEY", "")
PRIORITY = os.getenv("DOWNLOADER_PRIORITY", "normal").strip().lower()
PRIORITIES = {  # NZBGet append() priority / SABnzbd priority
    "nzbget": {"low": -50, "normal": 0, "high": 50, "veryhigh": 100, "force": 900},
    "sabnzbd": {"low": -1, "normal": 0, "high": 1, "veryhigh": 1, "force": 2},
}
DEFAULT_CATS = {
    "nzbget": {1000: "Software", 2000: "Movies", 3000: "Music", 4000: "Software", 5000: "Series"},
    "sabnzbd": {1000: "software", 2000: "movies", 3000: "audio", 4000: "software", 5000: "tv"},
}


def name():
    return {"nzbget": "NZBGet", "sabnzbd": "SABnzbd"}.get(KIND) if URL else None


def category_for(cat: int) -> str:
    raw = os.getenv("DOWNLOADER_CATEGORIES", "")
    mapping = {int(k): v for k, v in (p.split("=", 1) for p in raw.split(",") if "=" in p)} if raw \
        else DEFAULT_CATS.get(KIND, {})
    return mapping.get(cat, mapping.get(cat - cat % 1000, ""))


def _open(req):
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def send(nzb: str, title: str, cat: int):
    if not name():
        raise RuntimeError("No downloader configured (DOWNLOADER and DOWNLOADER_URL)")
    category = category_for(cat)
    if KIND == "nzbget":
        body = json.dumps({"method": "append", "params": [
            f"{title}.nzb", base64.b64encode(nzb.encode()).decode(), category,
            PRIORITIES["nzbget"].get(PRIORITY, 0), False, False, "", 0, "SCORE", []]}).encode()
        req = urllib.request.Request(f"{URL}/jsonrpc", body, {"Content-Type": "application/json"})
        if USER:
            req.add_header("Authorization", "Basic " + base64.b64encode(f"{USER}:{PASS}".encode()).decode())
        res = _open(req)
        if not res.get("result") or res.get("result", 0) <= 0:
            raise RuntimeError(f"NZBGet refused the NZB: {res.get('error') or res}")
        return
    # SABnzbd: multipart upload (mode=addfile)
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"name\"; filename=\"{title}.nzb\"\r\n"
            f"Content-Type: application/x-nzb\r\n\r\n{nzb}\r\n--{boundary}--\r\n").encode()
    q = urllib.parse.urlencode({"mode": "addfile", "apikey": APIKEY, "output": "json", "nzbname": title, "cat": category,
                               "priority": PRIORITIES["sabnzbd"].get(PRIORITY, 0)})
    req = urllib.request.Request(f"{URL}/api?{q}", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    res = _open(req)
    if not res.get("status"):
        raise RuntimeError(f"SABnzbd refused the NZB: {res.get('error') or res}")
