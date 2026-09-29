"""
Auto-Rename: filename parsing + template rendering (pure functions, no Telegram / DB).

Ported from the Auto-Rename bot's file_rename.py (episode / season / audio / quality
extractors, quality sort order) and extended with {title} {year} {codec} {source} {group}
{size} {filename} placeholders, episode ranges, movie-aware templates, tag cleaning,
per-user word rules, safe filename cleaning and word-boundary NSFW matching.
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
    re.compile(r'(?<![\dx])\d{1,2}x(\d{2,3})(?![\dp])', re.IGNORECASE),              # 2x05
    re.compile(r'\s-\s(\d{1,4})(?:v\d)?(?=\s*(?:$|[\[(]|END\b|FINAL\b))', re.IGNORECASE),  # "Show - 12 [1080p]"
    re.compile(r'\bE(\d+)\b', re.IGNORECASE),
    re.compile(r'[\[\(]E(\d+)[\]\)]', re.IGNORECASE),
    re.compile(r'\b(\d+)\s*of\s*\d+\b', re.IGNORECASE),
    re.compile(r'(?:^|[^0-9A-Z])(\d{1,4})(?:[^0-9A-Z]|$)(?!' + _EXCL + r')', re.IGNORECASE),
]
_NOT_EPISODES = {360, 480, 540, 576, 720, 1080, 1440, 2160, 264, 265}

_SEASON_PATTERNS = [
    re.compile(r'S(\d+)[._\s-]?EP?\d+', re.IGNORECASE),
    re.compile(r'(?<![\dx])(\d{1,2})x\d{2,3}(?![\dp])', re.IGNORECASE),
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
                    r'|(?<![\dx])\d{1,2}x\d{2,3}(?![\dp])|\b\d{3,4}x\d{3,4}\b'
                    r'|[\[\(]|\s-\s\d+', name, maxsplit=1)[0]
    name = re.sub(r'[._]+', ' ', name)
    name = re.sub(r'(?<!\d)(?:19|20)\d{2}(?!\d)\s*$', '', name)          # trailing year
    return re.sub(r'\s+', ' ', name).strip(" -_[]()")


def extract_episode_end(filename: str, start):
    """Last episode of a multi-episode file: S01E01-E03 / E01E02 / Episode 1-3 → 3 (None if single)."""
    if not filename or start is None:
        return None
    name = _norm(filename)
    pats = (rf'(?<![A-Za-z])EP?0*{start}(?:\s*[-~&+]\s*(?:EP?)?|E)0*(\d{{1,4}})(?![\dp])',
            rf'Episode[\s._-]*0*{start}\s*[-~&]\s*0*(\d{{1,4}})(?!\d|p)')
    for pat in pats:
        m = re.search(pat, name, re.IGNORECASE)
        if m:
            end = int(m.group(1))
            if start < end <= start + 50:
                return end
    return None


_SOURCE_PAT = re.compile(r'\b(WEB[-. ]?DL|WEB[-. ]?Rip|Blu[-. ]?Ray|BDRip|BRRip|HDRip|HDTV|DVDRip|DVDScr|'
                         r'HDCAM|CAMRip|HDTS|PreDVD|WEB)\b', re.IGNORECASE)
_SOURCE_CANON = {"webdl": "WEB-DL", "webrip": "WEBRip", "bluray": "BluRay", "bdrip": "BDRip", "brrip": "BRRip",
                 "hdrip": "HDRip", "hdtv": "HDTV", "dvdrip": "DVDRip", "dvdscr": "DVDScr", "hdcam": "HDCAM",
                 "camrip": "CAMRip", "hdts": "HDTS", "predvd": "PreDVD", "web": "WEB"}


def extract_source(filename: str):
    m = _SOURCE_PAT.search(_norm(filename or ""))
    if not m:
        return None
    return _SOURCE_CANON.get(re.sub(r"[-. ]", "", m.group(1)).lower(), m.group(1))


_NOT_GROUP = re.compile(r'(?i)^(?:\d{3,4}p|[24]k|x26[45]|hevc|avc|aac|ac3|dual|multi|hindi|eng|sub|dub|'
                        r'web-?dl|web-?rip|blu-?ray|hdrip|[0-9a-f]{8}|\d+)$')


def extract_group(filename: str):
    """Release group: leading [SubsPlease] or a trailing scene tag (…x264-RARBG)."""
    stem = _stem(filename or "")
    m = re.match(r'\s*\[([^\]]{2,24})\]', stem)
    if m and not _NOT_GROUP.match(m.group(1).strip()) and not m.group(1).strip().startswith("@"):
        return m.group(1).strip()
    m = re.search(r'[.\s]\S*[A-Za-z0-9]-([A-Za-z0-9]{2,15})$', stem)
    if m and "." in stem and not _NOT_GROUP.match(m.group(1)):
        return m.group(1)
    return None


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
    episode = extract_episode_number(filename)
    return {
        "season": extract_season_number(filename),
        "episode": episode,
        "episode_end": extract_episode_end(filename, episode),
        "quality": extract_quality(filename),
        "audio": extract_audio_info(filename),
        "year": extract_year(filename),
        "codec": extract_codec(filename),
        "title": extract_title(filename),
        "source": extract_source(filename),
        "group": extract_group(filename),
    }


# ─────────────────────────── templates ───────────────────────────
PLACEHOLDERS = ("{title}", "{season}", "{episode}", "{quality}", "{audio}", "{year}", "{codec}", "{source}",
                "{group}", "{size}", "{filename}")
_MISSING = "\x02"      # marks a season / episode the name doesn't have (stripped with its S / E prefix)
_BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def clean_filename(name: str, limit: int = 200) -> str:
    name = _BAD_CHARS.sub(" ", name or "")
    name = re.sub(r'\[[\s._-]*\]|\([\s._-]*\)|\{[\s._-]*\}', '', name)   # empty brackets left by missing values
    name = re.sub(r'\s+', ' ', name).strip(" .-_")
    return name[:limit].rstrip(" .")


def _pad(n) -> str:
    return f"{n:02d}"


def render(template: str, filename: str, info: dict = None) -> str:
    """
    Build the new name (without extension) from a user template.

    Brace placeholders: {title} {season} {episode} {quality} {audio} {year} {codec} {source} {group}
    {size} {filename}. Auto-Rename style words also work: SSeason → S01, EPEpisode → EP05, and the bare
    words Season / Episode / EP / Quality / Audio are replaced (e.g. "[SSeason] [EPEpisode] [Quality] [Audio]").

    Movie-aware: a missing season defaults to 01 only when an episode was found; a name with
    neither (a movie) drops the S··E·· part instead of inventing "S01E01", and a season pack
    without an episode keeps just "S02". Multi-episode files render {episode} as "01-03".
    """
    info = info or parse(filename)
    season, episode = info.get("season"), info.get("episode")
    e = _pad(episode) if episode is not None else _MISSING
    if episode is not None and info.get("episode_end"):
        e += "-" + _pad(info["episode_end"])
    if season is not None:
        s = _pad(season)
    else:
        s = "01" if episode is not None else _MISSING
    values = {
        "title": info.get("title") or "", "season": s, "episode": e,
        "quality": info.get("quality") or "", "audio": info.get("audio") or "",
        "year": info.get("year") or "", "codec": info.get("codec") or "",
        "source": info.get("source") or "", "group": info.get("group") or "",
        "size": info.get("size") or "", "filename": _stem(filename),
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
    # drop missing season / episode together with their prefix ("S", "E", "EP", "Season ", "Episode ")
    out = re.sub(r"(?i)(?<![A-Za-z])(?:season|episode|ep|s|e)?[ ._]?" + _MISSING, "", out)
    out = re.sub(r"\s[-–|](?:\s+[-–|])+\s", " - ", out.replace(_MISSING, ""))   # "Title - - 720p" → "Title - 720p"
    out = re.sub(r"^[-._\s]+", "", out)
    return clean_filename(out) or clean_filename(_stem(filename)) or "file"


# ─────────────────────────── cleaning & word rules ───────────────────────────
_TAG_PATTERNS = [
    re.compile(r"(?i)\b(?:https?://|t\.me/|telegram\.(?:me|dog)/)\S+"),
    re.compile(r"(?i)\bwww\.[\w.-]+"),
    re.compile(r"(?<![\w@])@[A-Za-z0-9_]{3,}"),
    re.compile(r"\b(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9-]{3,}\.(?:com|net|org|xyz|io|cc|site|club|link|vip|biz|info|"
               r"cam|pw|lol|ws|mx|ms|nl|ru|pk|bz|lat|top|store|online)\b"),
]


def clean_tags(name: str) -> str:
    """Strip @channel tags, t.me / http links and website names (www.x.com, x.net …)."""
    out = name or ""
    for pat in _TAG_PATTERNS:
        out = pat.sub(" ", out)
    out = re.sub(r"[\[(]\s*[\])]", " ", out)
    out = re.sub(r"^[\s._-]+|[\s_-]+(?=\.[^.]*$)", "", out)
    return re.sub(r"\s{2,}", " ", out).strip()


def parse_word_rules(text: str, limit: int = 30) -> list:
    """'old => new' / 'old | new' lines replace, a bare 'word' line removes. Returns [[old, new], …]."""
    rules = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.split(r"\s*(?:=>|->|\|)\s*", line, maxsplit=1)
        old = m[0].strip()
        new = m[1].strip() if len(m) > 1 else ""
        if old:
            rules.append([old[:60], new[:60]])
    return rules[:limit]


def apply_words(name: str, rules) -> str:
    """Case-insensitive; plain words match whole words only, anything else matches literally."""
    out = name or ""
    for old, new in rules or []:
        if not old:
            continue
        pat = re.escape(old)
        if re.fullmatch(r"\w+", old):
            pat = rf"(?<![A-Za-z0-9]){pat}(?![A-Za-z0-9])"
        out = re.sub(pat, lambda _m, n=new: n, out, flags=re.IGNORECASE)
    return out


def prepare_source(filename: str, clean: bool = False, words=None) -> str:
    """Tidy the *incoming* name before parsing (template text itself is never touched)."""
    if not clean and not words:
        return filename or ""
    stem, ext = os.path.splitext(filename or "")
    if ext and not re.fullmatch(r"\.[A-Za-z0-9]{1,6}", ext):
        stem, ext = filename, ""
    if clean:
        stem = clean_tags(stem)
    if words:
        stem = apply_words(stem, words)
    stem = re.sub(r"\s{2,}", " ", stem).strip(" ._-")
    return (stem or _stem(filename) or "file") + ext


def _target_ext(filename: str, to_mkv: bool) -> str:
    ext = os.path.splitext(filename or "")[1]
    if to_mkv and ext.lower() in (".mp4", ".m4v"):
        ext = ".mkv"
    if ext and not re.fullmatch(r"\.[A-Za-z0-9]{1,6}", ext):
        ext = ""
    return ext


def new_filename(template: str, filename: str, to_mkv: bool = False, clean: bool = False, words=None,
                 size: str = "") -> str:
    """Final name for auto-rename: source clean-up → template → safe characters → extension."""
    src = prepare_source(filename, clean, words)
    info = parse(src)
    info["size"] = size
    return render(template, src, info) + _target_ext(filename, to_mkv)


KNOWN_EXT = {"mkv", "mp4", "m4v", "avi", "mov", "webm", "flv", "wmv", "ts", "m2ts", "3gp", "mpg", "mpeg",
             "mp3", "m4a", "aac", "flac", "ogg", "opus", "wav", "wma", "ac3", "srt", "ass", "ssa", "vtt", "sub",
             "zip", "rar", "7z", "tar", "gz", "pdf", "epub", "cbz", "cbr", "apk", "exe", "txt", "jpg", "png"}


def manual_filename(new: str, old: str, to_mkv: bool = False) -> str:
    """A name the user typed: safe characters, and keep the original extension if they left it out."""
    stem, ext = os.path.splitext((new or "").strip())
    if not (ext and (ext[1:].lower() in KNOWN_EXT or ext.lower() == os.path.splitext(old or "")[1].lower())):
        stem, ext = (new or "").strip(), _target_ext(old, to_mkv)
    return (clean_filename(stem) or clean_filename(_stem(old)) or "file") + ext


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


# Never allowed – not for Premium, not even when an admin turns the NSFW filter off:
# anything that points at minors in a sexual context, or at non-consensual content.
FORBIDDEN_WORDS = {
    "pedo", "paedo", "pedophile", "paedophile", "pedophilia", "loli", "lolicon", "shota", "shotacon",
    "jailbait", "underage", "under age", "preteen", "pre-teen", "child porn", "childporn", "kiddie porn",
    "kiddy porn", "cp video", "cp videos", "toddlercon", "minor sex", "teen rape", "rape", "raped", "raping",
    "noncon", "non-con", "non consent", "non-consensual", "nonconsensual", "forced sex", "drugged sex",
    "hidden cam sex", "spycam sex", "revenge porn",
}
NSFW_WORDS |= FORBIDDEN_WORDS

_NSFW_ORDER = sorted(NSFW_WORDS, key=lambda w: (-len(w), w))   # deterministic, most specific first
_FORBIDDEN_ORDER = sorted(FORBIDDEN_WORDS, key=lambda w: (-len(w), w))


def _normalise(name: str) -> str:
    low = name.lower()
    for ok in EXCEPTIONS:
        low = low.replace(ok, " ")
    return " " + re.sub(r"[^a-z0-9+\-]+", " ", low.replace("_", " ").replace(".", " ")) + " "


def _match(words, names) -> str:
    for name in names:
        if not name:
            continue
        text = _normalise(name)
        for word in words:
            if f" {word} " in text:
                return word
    return ""


def is_nsfw(*names: str) -> str:
    """Return the matched keyword ('' if clean)."""
    return _match(_NSFW_ORDER, names)


def is_forbidden(*names: str) -> str:
    """Keyword of content that is blocked for everyone ('' if none)."""
    return _match(_FORBIDDEN_ORDER, names)
