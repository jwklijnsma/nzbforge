"""Metadata enrichment, all free:

TV via TVmaze (no key), movies via Cinemeta (no key; Stremio's public IMDb-based catalogue) or TMDB when
TMDB_API_KEY is set. Cinemeta also fills in shows TVmaze doesn't know, and movies TMDB doesn't find.
"""
import html
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from sqlalchemy import and_, or_, select

from .models import Release

log = logging.getLogger("enrich")
TMDB_KEY = os.getenv("TMDB_API_KEY", "")
PER_PASS = int(os.getenv("ENRICH_PER_PASS", "1000"))
WORKERS = int(os.getenv("ENRICH_WORKERS", "8"))
CINEMETA = os.getenv("CINEMETA", "1") == "1"
CINEMETA_URL = os.getenv("CINEMETA_URL", "https://v3-cinemeta.strem.io").rstrip("/")
CACHE_HOURS = 12
FIELDS = ("meta_title", "year", "overview", "poster_url", "rating", "imdbid", "tvdbid", "tmdbid", "genres", "website")


class _Rate:
    """Spaces requests to one site so parallel workers stay inside its rate limit."""

    def __init__(self, per_second):
        self.interval, self.next, self.lock = 1 / per_second, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next)
            self.next = at + self.interval
        time.sleep(max(0.0, at - now))


RATES = {"api.tvmaze.com": _Rate(2), "api.themoviedb.org": _Rate(20),  # TVmaze: 20 calls / 10 s
         urllib.parse.urlsplit(CINEMETA_URL).hostname: _Rate(5)}
_cache: dict = {}  # (kind, title, year) -> (time, info or None), kept between passes
_cache_lock = threading.Lock()


def _get(url):
    rate = RATES.get(urllib.parse.urlsplit(url).hostname)
    req = urllib.request.Request(url, headers={"User-Agent": "nzbforge/0.1", "Accept": "application/json"})
    for attempt in (1, 2):
        if rate:
            rate.wait()
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 1:  # rate limited: wait as asked, then retry once
                time.sleep(min(10.0, float(e.headers.get("Retry-After") or 2)))
                continue
            raise


def _clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def tvmaze(title, year=None):
    try:
        d = _get("https://api.tvmaze.com/singlesearch/shows?q=" + urllib.parse.quote(title))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    ext = d.get("externals") or {}
    return {
        "meta_title": d.get("name"),
        "year": int(d["premiered"][:4]) if d.get("premiered") else None,
        "overview": _clean(d.get("summary")),
        "poster_url": (d.get("image") or {}).get("medium"),
        "rating": (d.get("rating") or {}).get("average"),
        "imdbid": ext.get("imdb"),
        "tvdbid": ext.get("thetvdb"),
        "genres": ", ".join(d.get("genres") or []),
        "website": d.get("url"),
    }


TMDB_GENRES = {28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy", 80: "Crime", 99: "Documentary",
               18: "Drama", 10751: "Family", 14: "Fantasy", 36: "History", 27: "Horror", 10402: "Music",
               9648: "Mystery", 10749: "Romance", 878: "Science Fiction", 10770: "TV Movie", 53: "Thriller",
               10752: "War", 37: "Western"}


def tmdb_movie(title, year=None):
    q = {"api_key": TMDB_KEY, "query": title}
    if year:
        q["year"] = year
    results = _get("https://api.themoviedb.org/3/search/movie?" + urllib.parse.urlencode(q)).get("results") or []
    if not results:
        return None
    m = results[0]
    imdb = None
    try:
        imdb = _get(f"https://api.themoviedb.org/3/movie/{m['id']}/external_ids?api_key={TMDB_KEY}").get("imdb_id")
    except Exception:
        pass
    return {
        "meta_title": m.get("title"),
        "year": int(m["release_date"][:4]) if m.get("release_date") else None,
        "overview": _clean(m.get("overview")),
        "poster_url": f"https://image.tmdb.org/t/p/w342{m['poster_path']}" if m.get("poster_path") else None,
        "rating": round(m["vote_average"], 1) if m.get("vote_average") else None,
        "imdbid": imdb or None,
        "tmdbid": m["id"],
        "genres": ", ".join(TMDB_GENRES[g] for g in m.get("genre_ids") or [] if g in TMDB_GENRES),
        "website": f"https://www.themoviedb.org/movie/{m['id']}",
    }


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def cinemeta(kind, title, year=None):
    """Free, keyless lookup: search the catalogue, pick the best title/year match, then fetch its details."""
    ctype = "series" if kind == "tv" else "movie"
    q = urllib.parse.quote(title, safe="")
    metas = _get(f"{CINEMETA_URL}/catalog/{ctype}/top/search={q}.json").get("metas") or []
    if not metas:
        return None

    def score(m):
        y = (m.get("releaseInfo") or "")[:4]
        y = int(y) if y.isdigit() else None
        same_name = _norm(m.get("name")) == _norm(title)
        year_ok = not year or (y is not None and abs(y - year) <= 1)
        return (same_name and year_ok, year_ok, same_name)

    best = max(metas[:10], key=score)  # max() keeps the first (most relevant) one on ties
    if kind == "movie" and year and not score(best)[1]:
        return None  # wrong film is worse than no film
    d = _get(f"{CINEMETA_URL}/meta/{ctype}/{best['id']}.json").get("meta") or {}
    imdb = d.get("imdb_id") or best.get("imdb_id") or best["id"]
    y = (d.get("year") or d.get("releaseInfo") or best.get("releaseInfo") or "")[:4]
    try:
        rating = round(float(d.get("imdbRating")), 1)
    except (TypeError, ValueError):
        rating = None
    info = {
        "meta_title": d.get("name") or best.get("name"),
        "year": int(y) if y.isdigit() else None,
        "overview": _clean(d.get("description")),
        "poster_url": d.get("poster") or best.get("poster"),
        "rating": rating,
        "imdbid": imdb if str(imdb).startswith("tt") else None,
        "genres": ", ".join(d.get("genres") or d.get("genre") or []),
        "website": f"https://www.imdb.com/title/{imdb}/" if str(imdb).startswith("tt") else None,
    }
    if d.get("moviedb_id"):
        info["tmdbid"] = int(d["moviedb_id"])
    if d.get("tvdb_id"):
        info["tvdbid"] = int(d["tvdb_id"])
    return info


def _lookup(key):
    """First source that knows the title wins; a failure of one source falls through to the next."""
    kind, title, year = key
    sources = [tvmaze] if kind == "tv" else ([tmdb_movie] if TMDB_KEY else [])
    if CINEMETA:
        sources.append(lambda t, y: cinemeta(kind, t, y))
    error = None
    for src in sources:
        try:
            info = src(title, year)
        except Exception as e:  # e.g. one site down: try the next, retry next pass if all fail
            error = e
            continue
        if info:
            return info
    if error:
        raise error
    return None


def _known(db, kind, title, year):
    """Metadata another release with the same title already got (e.g. earlier episodes): no web lookup."""
    cats = Release.category.between(5000, 5999) if kind == "tv" else Release.category.between(2000, 2999)
    q = select(Release).where(cats, Release.title == title, Release.meta_title.is_not(None),
                              Release.genres.is_not(None))
    if kind == "movie" and year:
        q = q.where(Release.year == year)
    r = db.scalar(q.order_by(Release.enriched_at.desc()).limit(1))
    return {f: getattr(r, f) for f in FIELDS} if r else None


def enrich_pending(db, limit=PER_PASS):
    """Look up TV/movie releases without metadata.

    Order of preference: in-memory cache (kept between passes), metadata an earlier release with the
    same title already has, and only then TVmaze/TMDB, in parallel within each site's rate limit.
    """
    cats = [Release.category.between(5000, 5999)]
    if TMDB_KEY or CINEMETA:
        cats.append(Release.category.between(2000, 2999))
    rows = db.scalars(
        select(Release)
        .where(Release.title.is_not(None), or_(*cats),
               # new releases, plus ones matched before genres were stored
               or_(Release.enriched_at.is_(None), and_(Release.meta_title.is_not(None), Release.genres.is_(None))))
        .order_by(Release.posted.desc()).limit(limit)
    ).all()
    if not rows:
        return 0

    def key_of(r):
        kind = "tv" if r.category >= 5000 else "movie"
        return kind, r.title, r.year if kind == "movie" else None  # a show's year is its premiere, not the file's

    results, now = {}, time.time()
    with _cache_lock:
        for k, (t, info) in list(_cache.items()):
            if now - t > CACHE_HOURS * 3600:
                del _cache[k]
    for r in rows:
        k = key_of(r)
        if k in results:
            continue
        with _cache_lock:
            hit = _cache.get((k[0], k[1].lower(), k[2]))
        if hit:
            results[k] = hit[1]
        elif (info := _known(db, *k)) is not None:
            results[k] = info
    todo = [k for k in {key_of(r) for r in rows} if k not in results]
    web = 0
    if todo:
        with ThreadPoolExecutor(WORKERS, thread_name_prefix="enrich") as ex:
            for k, fut in [(k, ex.submit(_lookup, k)) for k in todo]:
                try:
                    results[k] = fut.result()
                    web += 1
                except Exception as e:  # network/rate limit: leave pending, try next pass
                    log.warning("lookup failed for %r: %s", k[1], e)
                    continue
                with _cache_lock:
                    _cache[(k[0], k[1].lower(), k[2])] = (now, results[k])

    found = 0
    for r in rows:
        k = key_of(r)
        if k not in results:
            continue  # lookup failed; stays pending
        info = results[k]
        if info:
            found += 1
            for f, v in info.items():
                if v not in (None, ""):
                    setattr(r, f, v)
            r.genres = info.get("genres") or ""
        if r.genres is None:
            r.genres = ""  # looked up, none known (stops re-checking every pass)
        r.enriched_at = datetime.utcnow()
    db.commit()
    log.info("enrich: %d of %d releases matched (%d web lookups)", found, len(rows), web)
    return found
