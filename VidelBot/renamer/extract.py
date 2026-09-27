"""
Auto-Rename: filename parsing + template rendering (pure functions, no Telegram / DB).

Ported from the Auto-Rename bot's file_rename.py (episode / season / audio / quality
extractors, quality sort order) and extended with {title} {year} {codec} {filename}
placeholders, safe filename cleaning and word-boundary NSFW matching.
"""
import os
import re

# ─────────────────────────── extractors ───────────────────────────
_QUALITY_AND_YEAR = [
    r'\d{2,4}[pP]', r'\dK', r'HD(?:RIP)?', r'WEB(?:-)?DL', r'BLURAY', r'X264', r'X265', r'HEVC',
    r'FHD', r'UHD', r'HDR', r'H\.264', r'H\.265', r'(?:19|20)\d{2}', r'Multi(?:audio)?', r'Dual(?:audio)?',
]
_EXCL = r'(?:' + '|'.join(f'(?:[\\s._-]*{ind})' for ind in _QUALITY_AND_YEAR) + r')'

_EPISODE_PATTERNS = [
    re.compile(r'S\d+[._\s-]?EP?(\d+)', re.IGNORECASE),
    re.compile(r'(?<![A-Za-z])(?:Episode|EP)[\s._-]*(\d+)', re.IGNORECASE),
    re.compile(r'\bE(\d+)\b', re.IGNORECASE),
    re.compile(r'[\[\(]E(\d+)[\]\)]', re.IGNORECASE),
    re.compile(r'\b(\d+)\s*of\s*\d+\b', re.IGNORECASE),
    re.compile(r'(?:^|[^0-9A-Z])(\d{1,4})(?:[^0-9A-Z]|$)(?!' + _EXCL + r')', re.IGNORECASE),
]
_NOT_EPISODES = {360, 480, 540, 576, 720, 1080, 1440, 2160, 264, 265}

_SEASON_PATTERNS = [
    re.compile(r'S(\d+)[._\s-]?EP?\d+', re.IGNORECASE),
    re.compile(r'Season[\s._-]*(\d+)', re.IGNORECASE),
    re.compile(r'\bS(\d+)\b(?!E\d|' + _EXCL + r')', re.IGNORECASE),
    re.compile(r'[\[\(]S(\d+)[\]\)]', re.IGNORECASE),
    re.compile(r'[._-]S(\d+)(?:[._-]|$)', re.IGNORECASE),
]


def _stem(filename: str) -> str:
    return os.path.splitext(filename or "")[0]


def _norm(filename: str) -> str:
    """Stem with underscores as spaces ("_" is a word char, so \\b would not split on it)."""
    return _stem(filename).replace("_", " ")


def extract_episode_number(filename: str):
    if not filename:
        return None
    name = _norm(filename)
    for pattern in _EPISODE_PATTERNS:
        for match in pattern.findall(name):
            ep_str = match[0] if isinstance(match, tuple) else match
            try:
                num = int(ep_str)
            except ValueError:
                continue
            if not 1 <= num <= 9999:
                continue
            if pattern is _EPISODE_PATTERNS[-1] and (num in _NOT_EPISODES or 1900 <= num <= 2099):
                continue   # resolution / codec / year – not an episode
            return num
    return None


def extract_season_number(filename: str):
    if not filename:
        return None
    name = _norm(filename)
    for pattern in _SEASON_PATTERNS:
        m = pattern.search(name)
        if m:
            try:
                num = int(m.group(1))
            except ValueError:
                continue
            if 1 <= num <= 99:
                return num
    return None


_AUDIO_KEYWORDS = [
    ("Hindi", r'Hindi'), ("English", r'English'), ("Telugu", r'Telugu'), ("Tamil", r'Tamil'),
    ("Malayalam", r'Malayalam'), ("Kannada", r'Kannada'), ("Bengali", r'Bengali'), ("Korean", r'Korean'),
    ("Eng", r'\bEng\b'), ("Jap", r'\bJap(?:anese)?\b'),
    ("Eng sub", r'Eng[\s._-]?sub'), ("Eng dub", r'Eng[\s._-]?dub'), ("Sub", r'\bSub(?:bed)?\b'),
    ("Dub", r'\bDub(?:bed)?\b'),
]
_AUDIO_CODECS = [("AAC", r'\bAAC'), ("AC3", r'\bAC3\b'), ("DDP", r'\bDDP'), ("DTS", r'\bDTS'),
                 ("MP3", r'\bMP3\b'), ("5.1", r'5\.1'), ("2.0", r'\b2\.0\b')]


def extract_audio_info(filename: str):
    """Languages / 'Multi' / 'Dual' / codecs found in the name, e.g. 'Dual Hindi Jap AAC'."""
    if not filename:
        return None
    name = _norm(filename)
    found = []
    if re.search(r'\bMulti(?:[\s._-]?audio)?\b', name, re.IGNORECASE):
        found.append("Multi")
    if re.search(r'\bDual(?:[\s._-]?audio)?\b|\[DUAL\]', name, re.IGNORECASE):
        found.append("Dual")
    for label, pat in _AUDIO_KEYWORDS:
        if re.search(pat, name, re.IGNORECASE):
            # "Eng sub" already covers "Eng" and "Sub"
            if label in ("Eng", "Sub") and ("Eng sub" in found or re.search(r'Eng[\s._-]?sub', name, re.I)):
                continue
            if label in ("Eng", "Dub") and re.search(r'Eng[\s._-]?dub', name, re.I):
                continue
            found.append(label)
    for label, pat in _AUDIO_CODECS:
        if re.search(pat, name, re.IGNORECASE):
            found.append(label)
    found = list(dict.fromkeys(found))
    return " ".join(found) if found else None


_QUALITY_PATTERNS = [
    re.compile(r'\b(4K|2K|2160p|1440p|1080p|720p|576p|540p|480p|360p|240p)\b', re.IGNORECASE),
    re.compile(r'\b(HD(?:RIP)?|WEB(?:-)?DL|WEB(?:-)?RIP|BLU-?RAY|BDRIP|HDTV|DVDRIP)\b', re.IGNORECASE),
    re.compile(r'\b(X264|X265|HEVC)\b', re.IGNORECASE),
]
_QUALITY_CANON = {"4k": "4K", "2k": "2K", "hdrip": "HDRip", "web-dl": "WEB-DL", "webdl": "WEB-DL",
                  "webrip": "WEBRip", "web-rip": "WEBRip", "bluray": "BluRay", "blu-ray": "BluRay",
                  "bdrip": "BDRip", "hdtv": "HDTV", "dvdrip": "DVDRip", "hd": "HD", "x264": "x264",
                  "x265": "x265", "hevc": "HEVC"}


def extract_quality(filename: str):
    if not filename:
        return None
    name = _norm(filename)
    for pattern in _QUALITY_PATTERNS:
        m = pattern.search(name)
        if m:
            q = m.group(1)
            return _QUALITY_CANON.get(q.lower(), q.lower() if q[-1] in "pP" else q)
    return None


def extract_year(filename: str):
    m = re.search(r'(?<!\d)((?:19|20)\d{2})(?!\d|p)', _norm(filename or ""))
    return m.group(1) if m else None


def extract_codec(filename: str):
    name = _norm(filename or "")
    m = re.search(r'\b(x264|x265|h\.?264|h\.?265|hevc|avc|av1|vp9)\b', name, re.IGNORECASE)
    if not m:
        return None
    c = m.group(1).lower().replace(".", "")
    return {"h264": "H.264", "h265": "H.265", "hevc": "HEVC", "avc": "AVC", "av1": "AV1", "vp9": "VP9"}.get(c, c)


def extract_title(filename: str) -> str:
    """Best-effort series / movie title: text before SxxEyy / Episode / quality tags."""
    name = _norm(filename or "")
    name = re.sub(r'^(\s*[\[\(][^\]\)]*[\]\)]\s*)+', '', name)          # leading [Group] (tags)
    name = re.sub(r'@\w+', ' ', name)                                    # @channel tags
    name = re.split(r'(?i)\bS\d{1,2}[._\s-]?EP?\d+|\bS\d{1,2}\b|\bSeason\b|\bEpisode\b|\bEP?[\s._-]?\d+\b'
                    r'|\b\d+\s*of\s*\d+\b|\b\d{3,4}p\b|\b[24]K\b|\b(?:HDRip|WEB-?DL|WEB-?Rip|Blu-?Ray|BDRip|HDTV|x26[45]|HEVC)\b'
                    r'|[\[\(]|\s-\s\d+', name, maxsplit=1)[0]
    name = re.sub(r'[._]+', ' ', name)
    name = re.sub(r'(?<!\d)(?:19|20)\d{2}(?!\d)\s*$', '', name)          # trailing year
    return re.sub(r'\s+', ' ', name).strip(" -_[]()")


def quality_order(filename: str) -> int:
    """Sort order used by sequence mode (360p → 4K, unknown last)."""
    order = {"240p": 0, "360p": 1, "480p": 2, "540p": 3, "576p": 3, "720p": 4, "1080p": 5,
             "1440p": 6, "2160p": 7, "4k": 7}
    m = re.search(r"(240p|360p|480p|540p|576p|720p|1080p|1440p|2160p|4k)\b", filename or "", re.IGNORECASE)
    return order.get(m.group(1).lower(), 9) if m else 9


def sort_key(filename: str):
    return (extract_season_number(filename) or 0, extract_episode_number(filename) or 0,
            quality_order(filename), (filename or "").lower())


def parse(filename: str) -> dict:
    return {
        "season": extract_season_number(filename),
        "episode": extract_episode_number(filename),
        "quality": extract_quality(filename),
        "audio": extract_audio_info(filename),
        "year": extract_year(filename),
        "codec": extract_codec(filename),
        "title": extract_title(filename),
    }


# ─────────────────────────── templates ───────────────────────────
PLACEHOLDERS = ("{title}", "{season}", "{episode}", "{quality}", "{audio}", "{year}", "{codec}", "{filename}")
_BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def clean_filename(name: str, limit: int = 200) -> str:
    name = _BAD_CHARS.sub(" ", name or "")
    name = re.sub(r'\[\s*\]|\(\s*\)|\{\s*\}', '', name)       # empty brackets left by missing values
    name = re.sub(r'\s+', ' ', name).strip(" .-_")
    return name[:limit].rstrip(" .")


def render(template: str, filename: str, info: dict = None) -> str:
    """
    Build the new name (without extension) from a user template.

    Brace placeholders: {title} {season} {episode} {quality} {audio} {year} {codec} {filename}
    Auto-Rename style words also work: SSeason → S01, EPEpisode → EP05, and the bare
    words Season / Episode / EP / Quality / Audio are replaced (e.g. "[SSeason] [EPEpisode] [Quality] [Audio]").
    Missing season / episode default to 01 like the original bot.
    """
    info = info or parse(filename)
    s = f"{info['season']:02d}" if info.get("season") is not None else "01"
    e = f"{info['episode']:02d}" if info.get("episode") is not None else "01"
    values = {
        "title": info.get("title") or "", "season": s, "episode": e,
        "quality": info.get("quality") or "", "audio": info.get("audio") or "",
        "year": info.get("year") or "", "codec": info.get("codec") or "",
        "filename": _stem(filename),
    }
    out = template or ""
    # brace placeholders → sentinels first, so keyword replacement never touches inserted values
    keys = list(values)
    for i, key in enumerate(keys):
        out = re.sub(r"\{" + key + r"\}", f"\x00{i}\x01", out, flags=re.IGNORECASE)
    # Auto-Rename compatible keywords (order matters: combined tokens first)
    out = re.sub(r"S(?:season)(?:\d+)?\b", lambda _m: "S" + s, out, flags=re.IGNORECASE)
    out = re.sub(r"EP(?:episode)\b", lambda _m: "EP" + e, out, flags=re.IGNORECASE)
    out = re.sub(r"\bseason\b", lambda _m: s, out, flags=re.IGNORECASE)
    out = re.sub(r"\bepisode\b|\bEP\b", lambda _m: e, out, flags=re.IGNORECASE)
    out = re.sub(r"\bquality\b", lambda _m: values["quality"], out, flags=re.IGNORECASE)
    out = re.sub(r"\baudio\b", lambda _m: values["audio"], out, flags=re.IGNORECASE)
    out = re.sub(r"\x00(\d+)\x01", lambda m: values[keys[int(m.group(1))]], out)
    return clean_filename(out) or clean_filename(_stem(filename)) or "file"


def new_filename(template: str, filename: str, to_mkv: bool = False) -> str:
    ext = os.path.splitext(filename or "")[1]
    if to_mkv and ext.lower() in (".mp4", ".m4v"):
        ext = ".mkv"
    if ext and not re.fullmatch(r"\.[A-Za-z0-9]{1,6}", ext):
        ext = ""
    return render(template, filename) + ext


# ─────────────────────────── anti-NSFW ───────────────────────────
# Keyword lists from the Auto-Rename bot, matched on whole words / phrases only
# (the original substring check rejected names like "Mass Effect" or "Classroom").
NSFW_WORDS = {
    "porn", "sex", "nude", "naked", "boobs", "tits", "pussy", "dick", "cock", "fuck", "blowjob",
    "cum", "orgasm", "shemale", "erotic", "masturbate", "anal", "hardcore", "bdsm", "fetish", "lingerie",
    "xxx", "milf", "threesome", "squirting", "butt plug", "dildo", "vibrator", "escort", "handjob",
    "striptease", "kinky", "pornstar", "sex tape", "spank", "swinger", "cumshot", "deepthroat", "orgy",
    "sex toy", "voyeur", "pornhwa", "netorare", "netori", "netorase", "eromanga", "incest", "stepmom",
    "stepsister", "stepbrother", "ntr", "gangbang", "golden shower", "pegging", "rimming", "rough sex",
    "dirty talk", "sex chat", "nude pic", "lewd", "titty", "penis", "vagina", "clitoris", "genitals",
    "kamasutra", "pedo", "rape", "bondage", "cum inside", "creampie", "sex slave", "sex doll",
    "sex machine", "oral sex", "slut", "whore", "skank", "cumdumpster", "ecchi", "doujin", "hentai",
    "smut", "futanari", "tentacle", "doujinshi", "yaoi", "shota", "loli", "eroge", "h-manga", "h-anime",
    "adult manga", "18+ anime", "18+ manga", "lewd anime", "lewd manga", "animated porn", "animated sex",
    "hentai game", "hentai manga", "hentai anime", "hentai video", "pr0n", "s3x", "n00d", "p0rn",
    "h3ntai", "h-ntai", "p0rnhwa", "l3wd", "s3xual", "sexual", "onlyfans", "brazzers", "xvideos", "xnxx",
}
EXCEPTIONS = ("nxivm", "classroom", "assassination", "geass", "sussex", "essex", "middlesex")


_NSFW_ORDER = sorted(NSFW_WORDS, key=lambda w: (-len(w), w))   # deterministic, most specific first


def is_nsfw(*names: str) -> str:
    """Return the matched keyword ('' if clean)."""
    for name in names:
        if not name:
            continue
        low = name.lower()
        for ok in EXCEPTIONS:
            low = low.replace(ok, " ")
        text = " " + re.sub(r"[^a-z0-9+\-]+", " ", low.replace("_", " ").replace(".", " ")) + " "
        for word in _NSFW_ORDER:
            if f" {word} " in text:
                return word
    return ""
