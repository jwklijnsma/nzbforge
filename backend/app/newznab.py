"""Newznab API: caps, RSS search results, query filtering."""
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape, quoteattr
import re
from sqlalchemy import select, func, or_, and_
from .models import Release
from .parse import searchname

CATS = {
    1000: ("Console", {}),
    2000: ("Movies", {2030: "SD", 2040: "HD", 2045: "UHD"}),
    3000: ("Audio", {3010: "MP3", 3030: "Audiobook", 3040: "Lossless"}),
    4000: ("PC", {4030: "Mac", 4050: "Games"}),
    5000: ("TV", {5030: "SD", 5040: "HD", 5045: "UHD"}),
    6000: ("XXX", {}),
    7000: ("Other", {7020: "Ebook"}),
}
TITLE = "nzbforge"


def cat_name(cid: int) -> str:
    parent = cid - cid % 1000
    pname, subs = CATS.get(parent, ("Other", {}))
    return pname if cid == parent else f"{pname} > {subs.get(cid, cid)}"


def error_xml(code: int, desc: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<error code="{code}" description={quoteattr(desc)}/>'


def caps_xml(base: str) -> str:
    cats = "".join(
        f'<category id="{pid}" name="{pname}">'
        + "".join(f'<subcat id="{sid}" name="{sn}"/>' for sid, sn in subs.items())
        + "</category>"
        for pid, (pname, subs) in CATS.items()
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<caps>'
        f'<server version="0.1" title="{TITLE}" strapline="" email="" url={quoteattr(base)}/>'
        '<limits max="100" default="50"/>'
        '<registration available="no" open="no"/>'
        "<searching>"
        '<search available="yes" supportedParams="q"/>'
        '<tv-search available="yes" supportedParams="q,tvdbid,imdbid,season,ep"/>'
        '<movie-search available="yes" supportedParams="q,imdbid,tmdbid"/>'
        "</searching>"
        f"<categories>{cats}</categories></caps>"
    )


def _id_cond(db, col, value):
    """Match by id; releases not enriched yet still match if they share a parsed title with one that is."""
    known = db.scalar(select(Release.title).where(col == value, Release.title.is_not(None)).limit(1))
    cond = col == value
    if known:
        cond = or_(cond, and_(col.is_(None), Release.searchname.like(f"{searchname(known)}%")))
    return cond


def _conditions(db, q, cat, season, ep, maxage, tvdbid=None, imdbid=None, tmdbid=None):
    conds = []
    if tvdbid and str(tvdbid).isdigit():
        conds.append(_id_cond(db, Release.tvdbid, int(tvdbid)))
    if tmdbid and str(tmdbid).isdigit():
        conds.append(_id_cond(db, Release.tmdbid, int(tmdbid)))
    digits = re.sub(r"\D", "", str(imdbid or ""))
    if digits:
        conds.append(_id_cond(db, Release.imdbid, f"tt{digits.zfill(7)}"))
    for term in (q or "").lower().replace("%", " ").replace("_", " ").split():
        conds.append(Release.searchname.like(f"%{term}%"))
    ids = [int(c) for c in (cat or "").split(",") if c.strip().isdigit()]
    if ids:
        conds.append(or_(*[
            Release.category.between(c, c + 999) if c % 1000 == 0 else Release.category == c
            for c in ids
        ]))
    if season and str(season).isdigit():
        conds.append(Release.season == int(season))
    if ep and str(ep).isdigit():
        conds.append(Release.episode == int(ep))
    if maxage and str(maxage).isdigit():
        conds.append(Release.posted >= datetime.utcnow() - timedelta(days=int(maxage)))
    return conds


def search(db, q=None, cat=None, season=None, ep=None, maxage=None, limit=50, offset=0,
           tvdbid=None, imdbid=None, tmdbid=None):
    conds = _conditions(db, q, cat, season, ep, maxage, tvdbid, imdbid, tmdbid)
    total = db.scalar(select(func.count()).select_from(Release).where(*conds)) or 0
    rows = db.scalars(
        select(Release).where(*conds).order_by(Release.posted.desc()).limit(limit).offset(offset)
    ).all()
    return rows, total


def _item(r, base, apikey) -> str:
    link = f"{base}/api?t=get&id={r.guid}&apikey={apikey}"
    pub = format_datetime(r.posted.replace(tzinfo=timezone.utc))
    parent = r.category - r.category % 1000
    attrs = [("category", parent)] + ([("category", r.category)] if r.category != parent else [])
    attrs += [("size", r.size), ("guid", r.guid), ("files", r.files), ("grabs", r.grabs),
              ("poster", r.poster), ("group", r.group_name), ("usenetdate", pub)]
    if r.season is not None:
        attrs.append(("season", f"S{r.season:02d}"))
    if r.episode is not None:
        attrs.append(("episode", f"E{r.episode:02d}"))
    if r.imdbid:
        attrs.append(("imdb", r.imdbid[2:]))
    if r.tvdbid:
        attrs.append(("tvdbid", r.tvdbid))
    if r.tmdbid:
        attrs.append(("tmdbid", r.tmdbid))
    if r.poster_url:
        attrs.append(("coverurl", r.poster_url))
    if r.rating:
        attrs.append(("rating", r.rating))
    if r.year:
        attrs.append(("year", r.year))
    attr_xml = "".join(f'<newznab:attr name="{k}" value={quoteattr(str(v))}/>' for k, v in attrs)
    return (
        f"<item><title>{escape(r.name)}</title>"
        f'<guid isPermaLink="false">{r.guid}</guid>'
        f"<link>{escape(link)}</link><pubDate>{pub}</pubDate>"
        f"<category>{escape(cat_name(r.category))}</category>"
        f"<description>{escape(r.overview or r.name)}</description>"
        f'<enclosure url={quoteattr(link)} length="{r.size}" type="application/x-nzb"/>'
        f"{attr_xml}</item>"
    )


def rss_xml(rows, total, offset, base, apikey) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" '
        'xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/">'
        f"<channel><title>{TITLE}</title><description>{TITLE} feed</description>"
        f'<newznab:response offset="{offset}" total="{total}"/>'
        + "".join(_item(r, base, apikey) for r in rows)
        + "</channel></rss>"
    )
