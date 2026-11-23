"""Usenet header scanner: NNTP -> collections -> releases.

Runs inside the web process by default (started from app.main, see SCANNER),
or standalone with: python -m app.scanner
"""
import hashlib
import logging
import os
import socket
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from .db import SessionLocal, init_db
from .models import Collection, Group, Release
from .enrich import enrich_pending
from .parse import classify, extract_title, parse_subject, searchname

log = logging.getLogger("scanner")
HOST = os.getenv("NNTP_HOST", "")
PORT = int(os.getenv("NNTP_PORT", "563"))
USER = os.getenv("NNTP_USER", "")
PASS = os.getenv("NNTP_PASS", "")
USE_SSL = os.getenv("NNTP_SSL", "1") == "1"
BATCH = int(os.getenv("SCAN_BATCH", "10000"))
INITIAL = int(os.getenv("INITIAL_ARTICLES", "200000"))
INTERVAL = int(os.getenv("SCAN_INTERVAL", "300"))
MIN_COMPLETION = float(os.getenv("MIN_COMPLETION", "0.90"))
EXPIRE_DAYS = int(os.getenv("EXPIRE_DAYS", "3"))
NFO_PER_PASS = int(os.getenv("NFO_PER_PASS", "100"))


class NNTP:
    """Minimal NNTP client (nntplib was removed in Python 3.13)."""

    def __init__(self, host, port, user, password, use_ssl=True):
        raw = socket.create_connection((host, port), timeout=120)
        self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host) if use_ssl else raw
        self.f = self.sock.makefile("rb")
        self._status()  # greeting
        if user:
            code, _ = self.cmd(f"AUTHINFO USER {user}")
            if code == 381:
                code, _ = self.cmd(f"AUTHINFO PASS {password}")
            if code != 281:
                raise RuntimeError("NNTP authentication failed")

    def _status(self):
        line = self.f.readline().decode("utf-8", "replace").strip()
        return int(line[:3]), line[4:]

    def cmd(self, c):
        self.sock.sendall((c + "\r\n").encode())
        return self._status()

    def _multiline(self):
        lines = []
        while True:
            line = self.f.readline()
            if not line or line == b".\r\n":
                return lines
            if line.startswith(b".."):
                line = line[1:]
            lines.append(line.rstrip(b"\r\n").decode("utf-8", "replace"))

    def group(self, name):
        code, msg = self.cmd(f"GROUP {name}")
        if code != 211:
            raise RuntimeError(f"GROUP {name}: {code} {msg}")
        _, first, last, _ = msg.split()[:4]
        return int(first), int(last)

    def xover(self, a, b):
        code, msg = self.cmd(f"XOVER {a}-{b}")
        if code != 224:
            raise RuntimeError(f"XOVER: {code} {msg}")
        return self._multiline()

    def _raw_multiline(self):
        lines = []
        while True:
            line = self.f.readline()
            if not line or line == b".\r\n":
                return lines
            if line.startswith(b".."):
                line = line[1:]
            lines.append(line.rstrip(b"\r\n"))

    def body(self, msgid):
        code, _ = self.cmd(f"BODY <{msgid.strip('<>')}>")
        return self._raw_multiline() if code == 222 else None

    def close(self):
        try:
            self.cmd("QUIT")
            self.sock.close()
        except Exception:
            pass


def parse_date(s):
    try:
        d = parsedate_to_datetime(s)
        return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d
    except Exception:
        return datetime.utcnow()


def ingest(db, group, lines):
    cache = {}
    for line in lines:
        f = line.split("\t")
        if len(f) < 7:
            continue
        info = parse_subject(f[1])
        if not info:
            continue
        poster, msgid, when = f[2][:255], f[4], parse_date(f[3])
        try:
            nbytes = int(f[6])
        except ValueError:
            nbytes = 0
        base = info["base"][:512]
        col = cache.get((poster, base))
        if col is None:
            col = db.scalar(select(Collection).where(
                Collection.group_id == group.id, Collection.poster == poster, Collection.base == base))
            if col is None:
                col = Collection(group_id=group.id, poster=poster, base=base,
                                 set_total=info["set_total"], posted=when, updated=when, data={})
                db.add(col)
            cache[(poster, base)] = col
        entry = col.data.setdefault(info["file"], {"total": info["parts_total"], "parts": {}})
        entry["parts"][str(info["part"])] = [msgid, nbytes]
        col.set_total = col.set_total or info["set_total"]
        col.updated = max(col.updated, when)
        col.posted = min(col.posted, when)
    for col in cache.values():
        flag_modified(col, "data")
    db.commit()


def make_release(db, col, group_name):
    guid = hashlib.sha1(f"{group_name}|{col.poster}|{col.base}".encode()).hexdigest()
    if db.scalar(select(Release.id).where(Release.guid == guid)):
        return False
    cat, season, episode = classify(col.base)
    title, year = extract_title(col.base)
    files_data = [
        {"name": n, "total": f["total"],
         "parts": sorted([int(k), v[0], v[1]] for k, v in f["parts"].items())}
        for n, f in sorted(col.data.items())
    ]
    size = sum(p[2] for fd in files_data for p in fd["parts"])
    files = sum(1 for n in col.data if not n.lower().endswith(".par2")) or 1
    db.add(Release(guid=guid, name=col.base, searchname=searchname(col.base), group_name=group_name,
                   poster=col.poster, posted=col.posted, size=size, category=cat, season=season,
                   episode=episode, files=files, files_data=files_data, title=title, year=year))
    return True


def finalize(db):
    now = datetime.utcnow()
    names = {}
    made = dropped = 0
    for col in db.scalars(select(Collection).order_by(Collection.updated).limit(20000)).all():
        have = sum(len(f["parts"]) for f in col.data.values())
        want = sum(f["total"] for f in col.data.values())
        comp = have / want if want else 0
        idle = now - col.updated
        has_main = any(not n.lower().endswith(".par2") for n in col.data)
        set_ok = col.set_total is None or len(col.data) >= col.set_total
        ready = (comp >= 0.99 and set_ok and idle > timedelta(minutes=10)) or \
                (comp >= MIN_COMPLETION and has_main and idle > timedelta(hours=2))
        if ready:
            gname = names.get(col.group_id) or names.setdefault(col.group_id, db.get(Group, col.group_id).name)
            made += make_release(db, col, gname)
            db.delete(col)
        elif idle > timedelta(days=EXPIRE_DAYS):
            db.delete(col)
            dropped += 1
    db.commit()
    log.info("finalize: %d releases created, %d expired", made, dropped)


def yenc_decode(lines):
    out, in_data = bytearray(), False
    for line in lines:
        if line.startswith(b"=ybegin"):
            in_data = True
            continue
        if line.startswith(b"=ypart"):
            continue
        if line.startswith(b"=yend"):
            break
        if not in_data:
            continue
        esc = False
        for b in line:
            if esc:
                out.append((b - 64 - 42) & 255)
                esc = False
            elif b == 61:  # "="
                esc = True
            else:
                out.append((b - 42) & 255)
    return bytes(out)


def fetch_nfos(nntp, db, limit=NFO_PER_PASS):
    """Download the first segment of each release's .nfo and store it as text."""
    now, done = datetime.utcnow(), 0
    for r in db.scalars(select(Release).where(Release.nfo_at.is_(None))
                        .order_by(Release.posted.desc()).limit(limit * 5)).all():
        if done >= limit:
            break
        r.nfo_at = now
        nfo = next((fd for fd in r.files_data if fd["name"].lower().endswith(".nfo") and fd["parts"]), None)
        if not nfo:
            continue
        done += 1
        try:
            lines = nntp.body(nfo["parts"][0][1])
        except Exception:
            log.exception("NFO fetch failed")
            break
        data = yenc_decode(lines) if lines else b""
        if data:
            data = data[:20000].replace(b"\x00", b"")
            try:
                r.nfo = data.decode("utf-8")
            except UnicodeDecodeError:
                r.nfo = data.decode("cp437", "replace")
    db.commit()
    log.info("nfo: fetched %d", done)


def scan_group(nntp, db, g):
    first, last = nntp.group(g.name)
    if g.last_article > last:  # provider reset numbering
        g.last_article = 0
    a = g.last_article + 1 if g.last_article else max(first, last - INITIAL)
    while a <= last:
        b = min(a + BATCH - 1, last)
        ingest(db, g, nntp.xover(a, b))
        g.last_article = b
        db.commit()
        log.info("%s: %d/%d", g.name, b, last)
        a = b + 1


def scan_pass():
    nntp = NNTP(HOST, PORT, USER, PASS, USE_SSL)
    try:
        with SessionLocal() as db:
            for g in db.scalars(select(Group).where(Group.active)).all():
                scan_group(nntp, db, g)
            finalize(db)
            enrich_pending(db)
            fetch_nfos(nntp, db)
    finally:
        nntp.close()


def run(stop: threading.Event | None = None):
    stop = stop or threading.Event()
    while not stop.is_set():
        try:
            scan_pass()
        except Exception:
            log.exception("scan pass failed")
        stop.wait(INTERVAL)


def enabled() -> bool:
    """SCANNER=1/0 forces it on/off; default (auto): on when NNTP_HOST is set."""
    mode = os.getenv("SCANNER", "auto").strip().lower()
    if mode in ("1", "true", "yes", "on"):
        return True
    if mode in ("0", "false", "no", "off"):
        return False
    return bool(HOST)


def start_background() -> threading.Event:
    """Start the scan loop in a daemon thread next to the web app. Returns the stop event."""
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    log.setLevel(logging.INFO)
    stop = threading.Event()
    threading.Thread(target=run, args=(stop,), name="scanner", daemon=True).start()
    log.info("scanner started in background (every %ss)", INTERVAL)
    return stop


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    init_db()
    run()
