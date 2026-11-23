import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from . import newznab as nz
from . import downloader, scanner
from .db import SessionLocal, init_db
from .models import Group, Release, User
from .nzb import build_nzb


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with SessionLocal() as db:
        if not db.scalar(select(func.count()).select_from(User)):
            key = os.getenv("ADMIN_KEY") or secrets.token_hex(16)
            db.add(User(username="admin", api_key=key, is_admin=True))
            db.commit()
            print(f"[nzbforge] created admin user, API key: {key}")
        scanner.tag_pending(db)  # Spotweb-style details for releases indexed before they existed
    stop = scanner.start_background() if scanner.enabled() else None
    yield
    if stop:
        stop.set()


app = FastAPI(title="nzbforge", lifespan=lifespan)


def get_db():
    with SessionLocal() as db:
        yield db


def xml(body: str) -> Response:
    return Response(body, media_type="application/xml")


def to_int(v, default, lo=0, hi=10**9):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


# ---------- Newznab API (what Prowlarr / Sonarr / Radarr talk to) ----------
@app.get("/api")
def newznab_api(request: Request, db=Depends(get_db)):
    p = request.query_params
    t = p.get("t", "")
    base = str(request.base_url).rstrip("/")
    if t == "caps":
        return xml(nz.caps_xml(base))

    user = db.scalar(select(User).where(User.api_key == p.get("apikey", "")))
    if not user:
        return xml(nz.error_xml(100, "Incorrect user credentials"))

    if t in ("search", "tvsearch", "movie"):
        limit = to_int(p.get("limit"), 50, 1, 100)
        offset = to_int(p.get("offset"), 0)
        rows, total = nz.search(
            db, q=p.get("q"), cat=p.get("cat"), season=p.get("season"),
            ep=p.get("ep"), maxage=p.get("maxage"), limit=limit, offset=offset,
            tvdbid=p.get("tvdbid"), imdbid=p.get("imdbid"), tmdbid=p.get("tmdbid"),
        )
        return xml(nz.rss_xml(rows, total, offset, base, user.api_key))

    if t in ("get", "details"):
        rel = db.scalar(select(Release).where(Release.guid == p.get("id", "")))
        if not rel:
            return xml(nz.error_xml(300, "No such item"))
        if t == "details":
            return xml(nz.rss_xml([rel], 1, 0, base, user.api_key))
        rel.grabs += 1
        db.commit()
        return Response(
            build_nzb(rel),
            media_type="application/x-nzb",
            headers={"Content-Disposition": f'attachment; filename="{rel.name}.nzb"'},
        )

    return xml(nz.error_xml(202, "No such function"))


# ---------- JSON API for the React front end ----------
def current_user(x_api_key: str = Header(""), db=Depends(get_db)) -> User:
    user = db.scalar(select(User).where(User.api_key == x_api_key))
    if not user:
        raise HTTPException(401, "Invalid API key")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Admin only")
    return user


@app.get("/web/me")
def me(user: User = Depends(current_user)):
    return {"username": user.username, "is_admin": user.is_admin, "downloader": downloader.name()}


INFO_FIELDS = ("resolution", "source", "video_codec", "hdr", "audio", "languages", "subtitles", "edition",
               "release_group", "genres")


def _summary(r: Release) -> dict:
    return {
        "guid": r.guid, "name": r.name, "category": r.category, "category_name": nz.cat_name(r.category),
        "size": r.size, "posted": r.posted.isoformat() + "Z", "grabs": r.grabs, "files": r.files,
        "group": r.group_name, "poster": r.poster, "title": r.meta_title or r.title, "year": r.year,
        "poster_url": r.poster_url, "rating": r.rating, "season": r.season, "episode": r.episode,
        **{f: getattr(r, f) for f in INFO_FIELDS},
    }


SORTS = {"date": Release.posted, "size": Release.size, "title": Release.searchname,
         "rating": Release.rating, "grabs": Release.grabs}


@app.get("/web/search")
def web_search(q: str = "", cat: str = "", resolution: str = "", source: str = "", lang: str = "",
               maxage: str = "", sort: str = "date", order: str = "desc", limit: int = 50, offset: int = 0,
               user: User = Depends(current_user), db=Depends(get_db)):
    conds = nz._conditions(db, q, cat, None, None, maxage)
    if resolution:
        conds.append(Release.resolution.in_(resolution.split(",")))
    if source:
        conds.append(Release.source.in_(source.split(",")))
    if lang == "dutch":  # spoken Dutch or Dutch subtitles, like Spotweb's NL filter
        conds.append(or_(Release.languages.like("%Dutch%"), Release.subtitles.like("%Dutch%")))
    elif lang == "nlsubs":
        conds.append(Release.subtitles.like("%Dutch%"))
    col = SORTS.get(sort, Release.posted)
    col = col.asc() if order == "asc" else col.desc()
    total = db.scalar(select(func.count()).select_from(Release).where(*conds)) or 0
    rows = db.scalars(select(Release).where(*conds).order_by(col.nulls_last(), Release.posted.desc())
                      .limit(to_int(limit, 50, 1, 200)).offset(to_int(offset, 0))).all()
    return {"total": total, "items": [_summary(r) for r in rows]}


@app.get("/web/categories")
def web_categories(user: User = Depends(current_user), db=Depends(get_db)):
    """Category tree with counts for the sidebar."""
    counts = dict(db.execute(select(Release.category, func.count()).group_by(Release.category)).all())
    tree = []
    for pid, (pname, subs) in nz.CATS.items():
        n = sum(v for k, v in counts.items() if pid <= k < pid + 1000)
        tree.append({"id": pid, "name": pname, "count": n,
                     "subs": [{"id": sid, "name": sn, "count": counts.get(sid, 0)} for sid, sn in subs.items()]})
    return {"total": sum(counts.values()), "categories": tree}


@app.get("/web/release/{guid}")
def web_release(guid: str, user: User = Depends(current_user), db=Depends(get_db)):
    r = db.scalar(select(Release).where(Release.guid == guid))
    if not r:
        raise HTTPException(404, "No such release")
    return {
        **_summary(r),
        "title": r.meta_title or r.title or r.name,
        "overview": r.overview, "imdbid": r.imdbid, "tvdbid": r.tvdbid, "tmdbid": r.tmdbid, "nfo": r.nfo,
        "website": r.website,
        "file_list": [
            {"name": fd["name"], "parts": len(fd["parts"]), "total": fd["total"],
             "size": sum(p[2] for p in fd["parts"])}
            for fd in r.files_data
        ],
    }


@app.post("/web/release/{guid}/send")
def send_to_downloader(guid: str, user: User = Depends(current_user), db=Depends(get_db)):
    r = db.scalar(select(Release).where(Release.guid == guid))
    if not r:
        raise HTTPException(404, "No such release")
    try:
        downloader.send(build_nzb(r), r.name, r.category)
    except Exception as e:
        raise HTTPException(502, str(e))
    r.grabs += 1
    db.commit()
    return {"ok": True, "downloader": downloader.name()}


@app.get("/web/stats")
def stats(user: User = Depends(current_user), db=Depends(get_db)):
    return {
        "releases": db.scalar(select(func.count()).select_from(Release)) or 0,
        "groups": db.scalar(select(func.count()).select_from(Group)) or 0,
        "newest": (lambda d: d.isoformat() + "Z" if d else None)(db.scalar(select(func.max(Release.posted)))),
    }


class GroupIn(BaseModel):
    name: str


@app.get("/web/groups")
def list_groups(admin: User = Depends(admin_user), db=Depends(get_db)):
    return [{"name": g.name, "last_article": g.last_article, "active": g.active}
            for g in db.scalars(select(Group).order_by(Group.name))]


@app.post("/web/groups")
def add_group(body: GroupIn, admin: User = Depends(admin_user), db=Depends(get_db)):
    name = body.name.strip().lower()
    if not name or db.scalar(select(Group).where(Group.name == name)):
        raise HTTPException(400, "Group is empty or already added")
    db.add(Group(name=name))
    db.commit()
    return {"ok": True}


class GroupImport(BaseModel):
    pattern: str = "alt.binaries.*"
    min_articles: int = 1


@app.post("/web/groups/import")
def import_groups(body: GroupImport, admin: User = Depends(admin_user), db=Depends(get_db)):
    if not scanner.HOST:
        raise HTTPException(400, "NNTP_HOST is not set")
    try:
        nntp = scanner.NNTP(scanner.HOST, scanner.PORT, scanner.USER, scanner.PASS, scanner.USE_SSL)
    except (OSError, RuntimeError) as e:
        raise HTTPException(502, f"Usenet server: {e}")
    try:
        added, matched = scanner.import_groups(db, nntp, body.pattern.strip() or "*", max(0, body.min_articles))
    except RuntimeError as e:
        raise HTTPException(502, f"Usenet server: {e}")
    finally:
        nntp.close()
    return {"matched": matched, "added": added}


# Serve the built React app (run `npm run build` in frontend/). Mounted last so /api and /web win.
DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="ui")
