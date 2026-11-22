"""Subject parsing + release classification (no dependencies, easy to test)."""
import re

FILE_RE = re.compile(r'"(?P<file>[^"]+)"\s*yEnc\s*\((?P<n>\d+)/(?P<t>\d+)\)', re.I)
SET_RE = re.compile(r'[\[\(]\s*(\d+)\s*/\s*(\d+)\s*[\]\)]')
STRIP = [re.compile(p, re.I) for p in (
    r'\.vol\d+[+-]\d+\.par2$',
    r'\.par2$',
    r'\.part\d+\.rar$',
    r'\.(rar|r\d{2}|\d{3}|zip|7z|nfo|nzb|sfv|srr|mkv|mp4|avi|m4v|iso|flac|mp3|epub|pdf)$',
)]


def _strip_ext(name: str) -> str:
    for rx in STRIP:
        new = rx.sub("", name)
        if new != name:
            return new
    return name


def parse_subject(subject: str):
    m = FILE_RE.search(subject)
    if not m or int(m["t"]) == 0:
        return None
    sm = SET_RE.search(subject[: m.start()])
    base = _strip_ext(_strip_ext(m["file"])).strip(" .-_")
    if not base:
        return None
    return {
        "file": m["file"],
        "part": int(m["n"]),
        "parts_total": int(m["t"]),
        "set_total": int(sm[2]) if sm else None,
        "base": base,
    }


TV_RE = re.compile(r"\bS(\d{1,2})[ ._-]?E(\d{1,3})\b|\b(\d{1,2})x(\d{2,3})\b", re.I)
SEASON_RE = re.compile(r"\bS(\d{1,2})\b", re.I)
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
UHD_RE = re.compile(r"2160p|\b4k\b|\buhd\b", re.I)
HD_RE = re.compile(r"720p|1080[pi]", re.I)
VIDEO_RE = re.compile(r"blu-?ray|web-?dl|webrip|hdtv|dvdrip|bdrip|x26[45]|h\.?26[45]|hevc|remux", re.I)
LOSSLESS_RE = re.compile(r"\bflac\b|\balac\b", re.I)
MP3_RE = re.compile(r"\bmp3\b|\b320kbps\b", re.I)


def searchname(name: str) -> str:
    return re.sub(r"[._\-]+", " ", name).lower().strip()


def classify(name: str):
    """-> (category, season, episode). Episode None means season pack / not TV."""
    res = "UHD" if UHD_RE.search(name) else "HD" if HD_RE.search(name) else "SD"
    tv = {"SD": 5030, "HD": 5040, "UHD": 5045}[res]
    movie = {"SD": 2030, "HD": 2040, "UHD": 2045}[res]
    m = TV_RE.search(name)
    if m:
        s, e = (m[1], m[2]) if m[1] else (m[3], m[4])
        return tv, int(s), int(e)
    sm = SEASON_RE.search(name)
    if sm and VIDEO_RE.search(name):
        return tv, int(sm[1]), None
    if YEAR_RE.search(name) and (VIDEO_RE.search(name) or res != "SD"):
        return movie, None, None
    if LOSSLESS_RE.search(name):
        return 3040, None, None
    if MP3_RE.search(name):
        return 3010, None, None
    return 7000, None, None


def extract_title(name: str):
    """'Show.Name.S02E05.1080p...' -> ('Show Name', None); 'Some.Movie.2021.2160p...' -> ('Some Movie', 2021)."""
    year, cut = None, len(name)
    m = TV_RE.search(name) or SEASON_RE.search(name)
    if m:
        cut = m.start()
    else:
        for ym in YEAR_RE.finditer(name):
            if ym.start() > 0:
                year, cut = int(ym.group()), ym.start()
                break
    title = re.sub(r"[._]+", " ", name[:cut]).strip(" -")
    if m:
        ty = re.search(r"\s((?:19|20)\d{2})$", title)
        if ty:
            year, title = int(ty[1]), title[: ty.start()].strip()
    title = re.sub(r"\s+", " ", title)
    return (title or None), year
