"""Usenet header scanner: NNTP -> collections -> releases.

Runs inside the web process by default (started from app.main, see SCANNER),
or standalone with: python -m app.scanner
"""
import fnmatch
import hashlib
import logging
import os
import socket
import ssl
import threading
import time
import zlib
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from .db import SessionLocal, init_db
from .models import Collection, Group, Release
from .enrich import enrich_pending
from .parse import categorize, extract_title, file_exts, parse_subject, release_info, searchname

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
CONNECTIONS = max(1, int(os.getenv("NNTP_CONNECTIONS", "8")))       # parallel connections for XOVER/BODY
COMPRESS = os.getenv("NNTP_COMPRESS", "1") == "1"                    # XFEATURE COMPRESS GZIP when offered
POSTPROCESS_EVERY = int(os.getenv("POSTPROCESS_EVERY", "300"))       # build releases/metadata during long pulls


# ---------- live job status (read by GET /web/jobs) ----------
_lock = threading.Lock()
STATUS = {
    "scanner": {"state": "idle", "group": None, "group_index": 0, "group_count": 0,
                "article": 0, "article_from": 0, "article_to": 0, "headers": 0,
                "started": None, "next_run": None, "last_pass": None, "queued": []},
    "import": {"state": "idle", "pattern": None, "matched": 0, "added": 0, "error": None,
               "started": None, "finished": None},
}
_wake = threading.Event()        # set to start the next pass now
_requested: set[str] = set()     # groups asked for via "pull now" (empty = all active groups)
_pass_lock = threading.Lock()    # one scan pass at a time
_loop_running = False


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


def _set(job, **kw):
    with _lock:
        STATUS[job].update(kw)


def status() -> dict:
    with _lock:
        snap = {k: dict(v) for k, v in STATUS.items()}
    snap["scanner"]["queued"] = sorted(_requested)
    snap["scanner"]["enabled"] = _loop_running
    return snap


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
        if COMPRESS:
            # Highwinds/Giganews style header compression; servers without it just answer 500
            code, _ = self.cmd("XFEATURE COMPRESS GZIP")
            self.compressed = code == 290

    compressed = False

    def close(self):
        try:
            self.cmd("QUIT")
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass

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
        # Overview lines start with an article number; a zlib (0x78) or gzip (0x1f) byte means compressed.
        first = self.f.peek(1)[:1]
        if first in (b"\x78", b"\x1f"):
            return self._compressed_multiline()
        return self._multiline()

    def _compressed_multiline(self):
        """zlib/gzip stream followed by '.\\r\\n'; inside it the usual dot-terminated lines."""
        d = zlib.decompressobj(47)  # auto-detect zlib or gzip header
        out = []
        while not d.eof:
            chunk = self.f.read1(262144)
            if not chunk:
                raise EOFError("connection closed inside compressed XOVER")
            out.append(d.decompress(chunk))
        tail = d.unused_data
        while not tail.endswith(b".\r\n"):
            more = self.f.read1(64)
            if not more:
                break
            tail += more
        lines = []
        for line in b"".join(out).split(b"\r\n"):
            if line == b".":
                break
            if line.startswith(b".."):
                line = line[1:]
            if line:
                lines.append(line.decode("utf-8", "replace"))
        return lines

    def list_active(self, pattern="*"):
        """[(name, first, last, flag)] from LIST ACTIVE (falls back to plain LIST)."""
        code, msg = self.cmd(f"LIST ACTIVE {pattern}")
        if code != 215:
            code, msg = self.cmd("LIST")
        if code != 215:
            raise RuntimeError(f"LIST: {code} {msg}")
        out = []
        for line in self._multiline():
            parts = line.split()
            if len(parts) >= 4 and fnmatch.fnmatchcase(parts[0], pattern):
                try:
                    out.append((parts[0].lower(), int(parts[2]), int(parts[1]), parts[3]))
                except ValueError:
                    continue
        return out

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




class Pool:
    """NNTP connections, one per worker thread, kept open for the whole pass.

    Fetches run on the workers while the caller saves the previous batch, so the
    network never waits for the database (and vice versa).
    """

    def __init__(self, size=None):
        self.size = size or CONNECTIONS
        self._local = threading.local()
        self._conns = []
        self._lock = threading.Lock()
        self.executor = ThreadPoolExecutor(self.size, thread_name_prefix="nntp")
        self.compressed = False

    def _conn(self):
        c = getattr(self._local, "conn", None)
        if c is None:
            c = NNTP(HOST, PORT, USER, PASS, USE_SSL)
            self._local.conn, self._local.group = c, None
            with self._lock:
                self._conns.append(c)
                self.compressed = self.compressed or getattr(c, "compressed", False)
        return c

    def _drop(self):
        c = getattr(self._local, "conn", None)
        self._local.conn = None
        if c is not None:
            with self._lock:
                if c in self._conns:
                    self._conns.remove(c)
            c.close()

    def _call(self, fn, group=None):
        for attempt in (1, 2):  # one reconnect on a dropped connection
            try:
                c = self._conn()
                if group and self._local.group != group:
                    c.group(group)
                    self._local.group = group
                return fn(c)
            except (OSError, EOFError, ValueError):
                self._drop()
                if attempt == 2:
                    raise

    def submit(self, fn, group=None):
        return self.executor.submit(self._call, fn, group)

    def connect(self):
        """Open the first connection now, so bad credentials fail the pass instead of every group."""
        self.submit(lambda c: None).result()

    def group(self, name):
        return self.submit(lambda c: c.group(name)).result()

    def xover(self, group, a, b):
        return self.submit(lambda c: c.xover(a, b), group)

    def body(self, msgid):
        return self.submit(lambda c: c.body(msgid))

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            conns, self._conns = self._conns, []
        for c in conns:
            c.close()


def parse_date(s):
    try:
        d = parsedate_to_datetime(s)
        return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d
    except Exception:
        return datetime.utcnow()


def ingest(db, group, lines):
    parsed = []
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
        parsed.append((info, poster, msgid, when, nbytes, info["base"][:512]))
    # one query for every collection this batch touches (instead of one per post)
    cache = {}
    bases = list({p[5] for p in parsed})
    for i in range(0, len(bases), 500):
        for col in db.scalars(select(Collection).where(Collection.group_id == group.id,
                                                       Collection.base.in_(bases[i:i + 500]))):
            cache[(col.poster, col.base)] = col
    touched = {}
    for info, poster, msgid, when, nbytes, base in parsed:
        col = cache.get((poster, base))
        if col is None:
            col = Collection(group_id=group.id, poster=poster, base=base,
                             set_total=info["set_total"], posted=when, updated=when, data={})
            db.add(col)
            cache[(poster, base)] = col
        touched[id(col)] = col
        entry = col.data.setdefault(info["file"], {"total": info["parts_total"], "parts": {}})
        entry["parts"][str(info["part"])] = [msgid, nbytes]
        col.set_total = col.set_total or info["set_total"]
        col.updated = max(col.updated, when)
        col.posted = min(col.posted, when)
    for col in touched.values():
        flag_modified(col, "data")
    db.commit()


def make_release(db, col, group_name):
    guid = hashlib.sha1(f"{group_name}|{col.poster}|{col.base}".encode()).hexdigest()
    if db.scalar(select(Release.id).where(Release.guid == guid)):
        return False
    files_data = [
        {"name": n, "total": f["total"],
         "parts": sorted([int(k), v[0], v[1]] for k, v in f["parts"].items())}
        for n, f in sorted(col.data.items())
    ]
    cat, season, episode = categorize(col.base, file_exts(files_data))
    title, year = extract_title(col.base)
    size = sum(p[2] for fd in files_data for p in fd["parts"])
    files = sum(1 for n in col.data if not n.lower().endswith(".par2")) or 1
    db.add(Release(guid=guid, name=col.base, searchname=searchname(col.base), group_name=group_name,
                   poster=col.poster, posted=col.posted, size=size, category=cat, season=season,
                   episode=episode, files=files, files_data=files_data, title=title, year=year,
                   info_at=datetime.utcnow(), **release_info(col.base)))
    return True


def tag_pending(db, limit=5000):
    """Fill the Spotweb-style details for releases indexed before they existed; re-check 'Other'."""
    rows = db.scalars(select(Release).where(Release.info_at.is_(None)).limit(limit)).all()
    for r in rows:
        for k, v in release_info(r.name).items():
            setattr(r, k, v)
        if r.category == 7000:
            r.category = categorize(r.name, file_exts(r.files_data))[0]
        r.info_at = datetime.utcnow()
    db.commit()
    if rows:
        log.info("details: tagged %d releases", len(rows))
    return len(rows)


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
    return made


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


def fetch_nfos(pool, db, limit=NFO_PER_PASS):
    """Download the first segment of each release's .nfo (in parallel) and store it as text."""
    now, todo = datetime.utcnow(), []
    for r in db.scalars(select(Release).where(Release.nfo_at.is_(None))
                        .order_by(Release.posted.desc()).limit(limit * 5)).all():
        if len(todo) >= limit:
            break
        r.nfo_at = now
        nfo = next((fd for fd in r.files_data if fd["name"].lower().endswith(".nfo") and fd["parts"]), None)
        if nfo:
            todo.append((r, pool.body(nfo["parts"][0][1])))
    done = 0
    for r, fut in todo:
        try:
            lines = fut.result()
        except Exception as e:
            log.warning("NFO fetch failed: %s", e)
            r.nfo_at = None  # try again next time
            continue
        data = yenc_decode(lines) if lines else b""
        if data:
            data = data[:20000].replace(b"\x00", b"")
            try:
                r.nfo = data.decode("utf-8")
            except UnicodeDecodeError:
                r.nfo = data.decode("cp437", "replace")
            done += 1
    db.commit()
    log.info("nfo: fetched %d", done)
    return done


def import_groups(db, nntp, pattern="alt.binaries.*", min_articles=1):
    """Add every group on the provider matching pattern (with >= min_articles). Returns (added, matched)."""
    have = set(db.scalars(select(Group.name)))
    matched = added = 0
    for name, first, last, flag in nntp.list_active(pattern):
        if flag.startswith(("=", "x")) or last - first + 1 < min_articles:
            continue  # aliases, removed groups, (almost) empty groups
        matched += 1
        with _lock:
            STATUS["import"]["matched"] = matched
            STATUS["import"]["added"] = added
        if name not in have:
            db.add(Group(name=name, server_first=first, server_last=last))
            have.add(name)
            added += 1
    db.commit()
    return added, matched


def scan_group(pool, db, g):
    first, last = pool.group(g.name)
    g.server_first, g.server_last, g.last_error = first, last, None
    if last < first or last == 0:
        g.scanned_at = datetime.utcnow()
        db.commit()
        return 0  # empty group
    if g.last_article > last:  # provider reset numbering
        g.last_article = 0
    a = g.last_article + 1 if g.last_article else max(first, last - INITIAL)
    _set("scanner", article_from=a, article_to=last, article=a - 1)
    ranges = deque((x, min(x + BATCH - 1, last)) for x in range(a, last + 1, BATCH))
    inflight = deque()  # in order, so last_article only ever moves forward over saved batches
    headers, t0 = 0, time.monotonic()
    try:
        while ranges or inflight:
            while ranges and len(inflight) < pool.size * 2:  # keep every connection busy
                x, y = ranges.popleft()
                inflight.append((y, pool.xover(g.name, x, y)))
            y, fut = inflight.popleft()
            lines = fut.result()
            ingest(db, g, lines)
            headers += len(lines)
            g.last_article = y
            db.commit()
            rate = headers / max(0.001, time.monotonic() - t0)
            with _lock:
                STATUS["scanner"]["article"] = y
                STATUS["scanner"]["headers"] += len(lines)
                STATUS["scanner"]["rate"] = round(rate)
            log.info("%s: %d/%d (%.0f headers/s)", g.name, y, last, rate)
    finally:
        for _, fut in inflight:
            fut.cancel()
    g.scanned_at = datetime.utcnow()
    db.commit()
    return headers


def _postprocess(pool, db, summary):
    """Turn finished collections into releases and look up metadata/NFOs."""
    _set("scanner", state="building releases", group=None)
    summary["releases"] += finalize(db) or 0
    tag_pending(db)
    _set("scanner", state="looking up metadata")
    enrich_pending(db)
    _set("scanner", state="fetching NFOs")
    fetch_nfos(pool, db)


def scan_pass(only=None):
    """One pass over all active groups (or just the names in `only`), then build/enrich releases.

    During long passes releases and metadata are also processed every POSTPROCESS_EVERY seconds,
    so new spots show up without waiting for the last group.
    """
    if not _pass_lock.acquire(blocking=False):
        return False  # a pass is already running; its caller will see the queued request
    t0 = datetime.utcnow()
    summary = {"groups": 0, "headers": 0, "releases": 0, "errors": [], "only": sorted(only) if only else None}
    _set("scanner", state="connecting", started=_iso(t0), headers=0, group=None, group_index=0, group_count=0,
         article=0, article_from=0, article_to=0, next_run=None, rate=0, connections=CONNECTIONS)
    pool = Pool()
    try:
        pool.connect()
        _set("scanner", compressed=pool.compressed)
        with SessionLocal() as db:
            q = select(Group).order_by(Group.name)
            q = q.where(Group.name.in_(only)) if only else q.where(Group.active)
            groups = db.scalars(q).all()
            last_post = time.monotonic()
            for i, g in enumerate(groups, 1):
                _set("scanner", state="scanning", group=g.name, group_index=i, group_count=len(groups),
                     article=0, article_from=0, article_to=0)
                try:
                    summary["headers"] += scan_group(pool, db, g) or 0
                    summary["groups"] += 1
                except RuntimeError as e:  # NNTP error for this group only; keep going
                    db.rollback()
                    g.last_error = str(e)[:255]
                    db.commit()
                    summary["errors"].append(f"{g.name}: {e}")
                    log.warning("%s: skipped (%s)", g.name, e)
                with _lock:
                    _requested.discard(g.name)
                if i < len(groups) and time.monotonic() - last_post > POSTPROCESS_EVERY:
                    _postprocess(pool, db, summary)
                    last_post = time.monotonic()
            _postprocess(pool, db, summary)
    except Exception as e:
        summary["errors"].append(str(e))
        raise
    finally:
        pool.close()
        summary["finished"] = _iso(datetime.utcnow())
        summary["seconds"] = round((datetime.utcnow() - t0).total_seconds())
        _set("scanner", state="idle", group=None, last_pass=summary)
        _pass_lock.release()
    return True


def run(stop: threading.Event | None = None):
    global _loop_running
    stop = stop or threading.Event()
    _loop_running = True
    try:
        while not stop.is_set():
            with _lock:
                only = set(_requested)
            try:
                scan_pass(only) if only else scan_pass()
            except Exception:
                log.exception("scan pass failed")
            if stop.is_set():
                break
            with _lock:
                pending = bool(_requested)
            if not pending:
                _set("scanner", state="waiting", next_run=_iso(datetime.utcnow() + timedelta(seconds=INTERVAL)))
                _wake.wait(INTERVAL)
            _wake.clear()
    finally:
        _loop_running = False


def request_pull(groups=None):
    """'Pull now' from the UI: wake the loop (or run a one-off pass when the loop is off)."""
    if groups:
        with _lock:
            _requested.update(groups)
    if _loop_running:
        _wake.set()
        return
    if _pass_lock.locked():
        return  # one-off pass already running; requested groups are picked up next time

    def once():
        with _lock:
            only = set(_requested)
        try:
            scan_pass(only) if only else scan_pass()
        except Exception:
            log.exception("scan pass failed")

    threading.Thread(target=once, name="scanner-once", daemon=True).start()


def start_import(pattern="alt.binaries.*", min_articles=1):
    """Run import_groups() in the background; progress in STATUS['import']."""
    with _lock:
        if STATUS["import"]["state"] == "running":
            return False
        STATUS["import"].update(state="running", pattern=pattern, matched=0, added=0, error=None,
                                started=_iso(datetime.utcnow()), finished=None)

    def job():
        try:
            nntp = NNTP(HOST, PORT, USER, PASS, USE_SSL)
            try:
                with SessionLocal() as db:
                    added, matched = import_groups(db, nntp, pattern, min_articles)
            finally:
                nntp.close()
            _set("import", state="done", added=added, matched=matched)
        except Exception as e:
            log.warning("group import failed: %s", e)
            _set("import", state="failed", error=str(e))
        finally:
            _set("import", finished=_iso(datetime.utcnow()))

    threading.Thread(target=job, name="group-import", daemon=True).start()
    return True


def wake():
    _wake.set()


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
