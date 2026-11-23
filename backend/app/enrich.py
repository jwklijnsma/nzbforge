"""Metadata enrichment: TV via TVmaze (no key), movies via TMDB (set TMDB_API_KEY)."""
import html
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from sqlalchemy import and_, or_, select

from .models import Release

log = logging.getLogger("enrich")
TMDB_KEY = os.getenv("TMDB_API_KEY", "")
PER_PASS = int(os.getenv("ENRICH_PER_PASS", "200"))


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "nzbforge/0.1", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


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


def enrich_pending(db, limit=PER_PASS):
    """Look up TV/movie releases that have no metadata yet. Lookups are cached per title for the pass."""
    cats = [Release.category.between(5000, 5999)]
    if TMDB_KEY:
        cats.append(Release.category.between(2000, 2999))
    rows = db.scalars(
        select(Release)
        .where(Release.title.is_not(None), or_(*cats),
               # new releases, plus ones matched before genres were stored
               or_(Release.enriched_at.is_(None), and_(Release.meta_title.is_not(None), Release.genres.is_(None))))
        .order_by(Release.posted.desc()).limit(limit)
    ).all()
    cache, found = {}, 0
    for r in rows:
        kind = "tv" if r.category >= 5000 else "movie"
        key = (kind, r.title.lower(), r.year)
        if key not in cache:
            try:
                cache[key] = tvmaze(r.title, r.year) if kind == "tv" else tmdb_movie(r.title, r.year)
            except Exception as e:  # network/rate limit: leave pending, try next pass
                log.warning("lookup failed for %r: %s", r.title, e)
                continue
            time.sleep(0.5)
        info = cache[key]
        if info:
            found += 1
            for k, v in info.items():
                if v not in (None, ""):
                    setattr(r, k, v)
            r.genres = info.get("genres") or ""
        if r.genres is None:
            r.genres = ""  # looked up, none known (stops re-checking every pass)
        r.enriched_at = datetime.utcnow()
    db.commit()
    log.info("enrich: %d of %d releases matched", found, len(rows))
