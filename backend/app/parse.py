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


# ---------- Spotweb-style release details, parsed from the name ----------
def _rx(p):
    return re.compile(p, re.I)


RESOLUTION = [("2160p", _rx(r"2160p|\b4k\b|\buhd\b")), ("1080p", _rx(r"1080[pi]")), ("720p", _rx(r"720p")),
              ("576p", _rx(r"576[pi]")), ("480p", _rx(r"480[pi]"))]
SOURCE = [("Remux", _rx(r"\bremux\b")), ("BluRay", _rx(r"blu-?ray|\bbd(rip|r|25|50|66|100)?\b|\bbrrip\b|\bbdmv\b")),
          ("WEBRip", _rx(r"web-?rip")), ("WEB-DL", _rx(r"web-?dl|\bweb\b|\b(amzn|nf|dsnp|hmax|atvp|vrt|npo|videoland)\b")),
          ("HDTV", _rx(r"\b(hdtv|pdtv|dvb|sdtv)\b")), ("DVD", _rx(r"\bdvd(rip|r|5|9)?\b")),
          ("CAM", _rx(r"\b(hd)?(cam|ts|telesync|telecine|tc)\b"))]
VIDEO = [("AV1", _rx(r"\bav1\b")), ("x265", _rx(r"x\.?265|h\.?265|\bhevc\b")), ("x264", _rx(r"x\.?264|h\.?264|\bavc\b")),
         ("XviD", _rx(r"\b(xvid|divx)\b")), ("VC-1", _rx(r"\bvc-?1\b")), ("MPEG-2", _rx(r"\bmpeg-?2\b"))]
HDR = [("Dolby Vision", _rx(r"\b(dv|dovi|dolby[ ._-]?vision)\b")), ("HDR10+", _rx(r"\bhdr10(\+|plus)")),
       ("HDR10", _rx(r"\bhdr10\b(?!\+)")), ("HDR", _rx(r"\bhdr\b"))]
AUDIO = [("TrueHD Atmos", _rx(r"truehd.{0,12}atmos|atmos.{0,12}truehd")), ("TrueHD", _rx(r"\btruehd\b")),
         ("DTS-HD MA", _rx(r"dts-?hd[ ._-]?ma")), ("DTS:X", _rx(r"\bdts-?x\b")), ("DTS", _rx(r"\bdts(-?hd)?\b")),
         ("DD+ Atmos", _rx(r"(ddp|dd\+|e-?ac-?3).{0,8}atmos")), ("DD+", _rx(r"\bddp|\bdd\+|\be-?ac-?3\b")),
         ("DD", _rx(r"\bdd[ ._]?[257][ ._]?[01]\b|\bac-?3\b")), ("AAC", _rx(r"\baac")), ("FLAC", _rx(r"\bflac\b")),
         ("Opus", _rx(r"\bopus\b")), ("MP3", _rx(r"\bmp3\b"))]
SUBS = [("Dutch", _rx(r"\b(nl[ ._-]?subs?|subs?[ ._-]?nl|nlsubbed|dutch[ ._-]?subs?|nl[ ._-]?subbed)\b")),
        ("English", _rx(r"\b(eng?[ ._-]?subs?|english[ ._-]?subs?)\b")),
        ("Multi", _rx(r"\bmulti[ ._-]?subs?\b"))]
LANGS = [("Dutch", _rx(r"\b(dutch|nl|nld|flemish|vlaams|nederlands)\b")), ("English", _rx(r"\b(english|eng)\b")),
         ("German", _rx(r"\b(german|ger|deutsch)\b")), ("French", _rx(r"\b(french|truefrench|vff|vf)\b")),
         ("Spanish", _rx(r"\b(spanish|spa|castellano|latino)\b")), ("Italian", _rx(r"\b(italian|ita)\b")),
         ("Multi", _rx(r"\bmulti\b"))]
EDITION = [("Repack", _rx(r"\b(repack|rerip)\b")), ("Proper", _rx(r"\bproper\b")), ("Extended", _rx(r"\bextended\b")),
           ("Unrated", _rx(r"\bunrated\b")), ("Director's Cut", _rx(r"\bdirectors?[ ._-]?cut\b|\bdc\b")),
           ("Remastered", _rx(r"\bremaster(ed)?\b")), ("IMAX", _rx(r"\bimax\b")), ("3D", _rx(r"\b3d\b")),
           ("Complete", _rx(r"\bcomplete\b"))]
GROUP_RE = re.compile(r"-([A-Za-z0-9]{2,20})$")


def _first(table, s):
    return next((label for label, rx in table if rx.search(s)), None)


def _all(table, s):
    found = [label for label, rx in table if rx.search(s)]
    return ", ".join(found) or None


def release_info(name: str) -> dict:
    """'Movie.2023.NL.Subs.2160p.WEB-DL.DDP5.1.Atmos.DV.HDR.H.265-GRP' -> resolution, source, codecs, languages..."""
    s = name.replace("_", ".")
    subs = _all(SUBS, s)
    no_subs = s
    for _, rx in SUBS:
        no_subs = rx.sub(" ", no_subs)  # "NL.Subs" is a subtitle, not the spoken language
    hdr = [label for label, rx in HDR if rx.search(s)]
    if "HDR10+" in hdr and "HDR10" in hdr:
        hdr.remove("HDR10")
    if "HDR" in hdr and len(hdr) > 1 and any(h.startswith("HDR10") for h in hdr):
        hdr.remove("HDR")
    m = GROUP_RE.search(s)
    group = m[1] if m and not re.fullmatch(r"\d+|S\d+E\d+|\d{3,4}p|x26[45]|h26[45]|flac|mp3|web|aac|dts|atmos|hevc|avc", m[1], re.I) else None
    return {
        "resolution": _first(RESOLUTION, s),
        "source": _first(SOURCE, s),
        "video_codec": _first(VIDEO, s),
        "hdr": ", ".join(hdr) or None,
        "audio": _first(AUDIO, s),
        "languages": _all(LANGS, no_subs),
        "subtitles": subs,
        "edition": _all(EDITION, s),
        "release_group": group,
    }


# ---------- extra categories (Spotweb: games, apps, books, erotica) ----------
XXX_RE = _rx(r"\bxxx\b|\bporn\b")
CONSOLE_RE = _rx(r"\b(nsw|switch|ps[345]|psv|xbox(one|360|series)?|wii ?u?|3ds|nds)\b")
PCGAME_RE = _rx(r"-(codex|plaza|skidrow|rune|tenoke|flt|doge|empress|razor1911|tinyiso|darksiders|hoodlum|reloaded"
                r"|prophet|elamigos|fitgirl|dodi)$|\bgog\b|\bfitgirl\b|\belamigos\b")
MAC_RE = _rx(r"\b(macos|mac ?os ?x|osx)\b")
APP_RE = _rx(r"\b(x64|x86|win(dows)?(32|64)?|multilingual|portable|keygen|incl|retail|activator)\b|\bv\d+(\.\d+){1,3}\b")
BOOK_RE = _rx(r"\b(e-?book|epub|mobi|azw3|retail ebook)\b")
AUDIOBOOK_RE = _rx(r"\b(audiobook|luisterboek)\b")
BOOK_EXT = {"epub", "mobi", "azw3", "azw", "cbr", "cbz", "pdf"}
CONSOLE_EXT = {"nsp", "xci", "pkg", "wbfs", "nsz"}
APP_EXT = {"exe", "msi", "dmg", "pkg", "apk"}


def classify_extra(name: str, exts=()) -> int | None:
    """Categories classify() doesn't know. -> Newznab id or None."""
    exts = {e.lower() for e in exts}
    if XXX_RE.search(name):
        return 6000
    if AUDIOBOOK_RE.search(name):
        return 3030
    if exts & BOOK_EXT or BOOK_RE.search(name):
        return 7020
    if exts & CONSOLE_EXT or CONSOLE_RE.search(name):
        return 1000
    if PCGAME_RE.search(name):
        return 4050
    if MAC_RE.search(name):
        return 4030
    if exts & APP_EXT or APP_RE.search(name):
        return 4000
    return None


def categorize(name: str, exts=()):
    """classify() + the extra Spotweb-style categories for anything that would end up in Other."""
    cat, season, episode = classify(name)
    if XXX_RE.search(name):
        return 6000, None, None
    if cat == 7000:
        cat = classify_extra(name, exts) or 7000
    return cat, season, episode


def file_exts(files_data) -> set:
    out = set()
    for f in files_data or []:
        n = f["name"].lower()
        if "." in n and not n.endswith((".par2", ".sfv", ".nfo", ".nzb")):
            ext = n.rsplit(".", 1)[1]
            if not re.fullmatch(r"r\d\d|\d{3}|rar|zip|7z", ext):
                out.add(ext)
    return out
