

#
import asyncio
import os
import re
import shlex
import shutil
import socket
import sys
import time

import aiohttp
import httpx

from async_lru import alru_cache
from py_yt import VideosSearch
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message
def _get_youtube_dl():
    from yt_dlp import YoutubeDL
    return YoutubeDL

import config
from AoiMusic.utils.decorators import asyncify
from AoiMusic.utils.formatters import seconds_to_min, time_to_seconds
from AoiMusic.utils.notify import notify_owner

_LOG_TAG = "Youtube"
_YT_SEARCH_TIMEOUT = 10
_YT_DLP_TIMEOUT = 60
_YT_SUBPROCESS_TIMEOUT = 60
_YT_STREAM_CHECK_TIMEOUT = 8


def _log(level, msg, *a, **kw):
    import logging
    logger = logging.getLogger("AoiMusic.platforms.Youtube")
    getattr(logger, level)(f"[{_LOG_TAG}] {msg}", *a, **kw)


def _query_variants(q: str) -> list:
    """Rephrase a misremembered/lyric-style title into progressively broader
    searches.

    Users type half-remembered lines like "aaye haaye jawani le doobi" while
    the actual title is only "Jawaani Le Doobi". Word-trimming recovers that
    with no external knowledge base: the full phrase comes first so anything
    that resolved before keeps resolving identically.
    """
    words = q.split()
    if len(words) < 3:
        return [q]
    n = len(words)
    seen, out = set(), []
    # Filler words usually sit in front, so lead-trims come before tail-trims.
    for cut in range(1, n):
        if n - cut >= 2:
            v = " ".join(words[cut:])
            if v not in seen:
                seen.add(v)
                out.append(v)
        if n - cut >= 2:
            v = " ".join(words[: n - cut])
            if v not in seen:
                seen.add(v)
                out.append(v)
    return [q] + out


def yt_dlp_binary():
    """Locate the yt-dlp binary even if it's outside the process PATH.

    Prefer the yt-dlp sitting next to the running interpreter so the
    subprocess uses the same Python as the bot. A stray ~/.local/bin/yt-dlp
    tied to an older interpreter would otherwise emit yt-dlp's
    "Support for Python version 3.10 has been deprecated" warning.
    """
    alongside = os.path.join(os.path.dirname(sys.executable), "yt-dlp")
    if os.path.isfile(alongside) and os.access(alongside, os.X_OK):
        return alongside
    path = shutil.which("yt-dlp")
    if path:
        return path
    for candidate in (
        os.path.expanduser("~/.local/bin/yt-dlp"),
        os.path.expanduser("~/bin/yt-dlp"),
        "/usr/local/bin/yt-dlp",
        "/usr/bin/yt-dlp",
        "/usr/sbin/yt-dlp",
    ):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return "yt-dlp"


def _valid_netscape(path: str) -> bool:
    try:
        with open(path) as f:
            header = f.read(200)
    except Exception as e:
        _log("warning", "failed to read cookie file %s: %s", path, e)
        return False
    return "# Netscape HTTP Cookie File" in header or "# HTTP Cookie File" in header


def cookies():
    # Explicit path wins: typically YT_COOKIES pointing at the media API's
    # yt-dlp-refreshed cookie file, which stays warm without any cron job.
    env_path = str(getattr(config, "YT_COOKIES", "") or "").strip()
    if env_path and os.path.isfile(env_path):
        if _valid_netscape(env_path):
            _log("info", "using cookie file: %s", env_path)
            return env_path
        _log("warning", "YT_COOKIES file is not a Netscape cookie file: %s", env_path)
    folder_path = f"{os.getcwd()}/cookies"
    if not os.path.isdir(folder_path):
        _log("warning", "cookies folder not found at %s", folder_path)
        return None
    txt_files = [file for file in os.listdir(folder_path) if file.endswith(".txt")]
    if not txt_files:
        _log("warning", "no .txt files in cookies folder")
        return None
    for cookie_txt_file in txt_files:
        cookie_txt_file = os.path.join(folder_path, cookie_txt_file)
        if _valid_netscape(cookie_txt_file):
            _log("info", "using cookie file: %s", cookie_txt_file)
            return cookie_txt_file
    _log("warning", "no valid Netscape cookie file found")
    return None


# --- Proxy and yt-dlp settings ---
from config import PROXY_URL
PROXY = PROXY_URL if PROXY_URL else None
RUNTIME_PRIORITY = ["node", "bun", "deno"]

# YouTube SABR experiment blocks adaptive bestaudio without PO tokens.
# Progressive itag 18 (360p mp4) still works with cookies + tv/mweb/web clients.
# tv_samsung rides at the tail as a rescue ladder: on this datacentre IP the
# logged-in tv/mweb/web combo was observed collapsing to itag 18 only, while
# tv_samsung alone returned the full ladder up to 2160p (25 video + 12 audio).
_YT_CLIENTS = ["tv_samsung", "tv", "mweb", "web"]
_YT_CLIENTS_STR = "tv,mweb,web,tv_samsung"
_INNERTUBE_NEXT_URL = "https://www.youtube.com/youtubei/v1/next?prettyPrint=false"
_AUDIO_FMT = "bestaudio/best/18/worst"
_VIDEO_FMT = (
    "bestvideo[height<=?2160][ext=mp4]+bestaudio[ext=m4a]/"
    "bestvideo[height<=?2160]+bestaudio/"
    "best[height<=?2160]/18/best"
)
# File downloads have no encoder ceiling: reach up to 8K (itag 272) when the
# source offers it. VC streaming stays on _VIDEO_FMT — Telegram's call video
# caps at 4K, and an 8K source only re-encodes hotter for nothing.
_VIDEO_FMT_8K = (
    "bestvideo[height<=?4320][ext=mp4]+bestaudio[ext=m4a]/"
    "bestvideo[height<=?4320]+bestaudio/"
    "best[height<=?4320]/best[height<=?2160][ext=mp4]+bestaudio[ext=m4a]/"
    "best[height<=?2160]+bestaudio/18/best"
)

# --- WPC PO Token Provider settings ---
# Uses a browser (yt-dlp-getpot-wpc + nodriver) to mint PO Tokens, bypassing
# YouTube's "Sign in to confirm you're not a bot" checks.
# Set WPC_BROWSER_PATH env var to override auto-detection.
def _find_browser():
    env = os.getenv("WPC_BROWSER_PATH", "").strip()
    if env and os.path.isfile(env):
        return env
    for name in ("chromium-browser", "chromium", "google-chrome", "google-chrome-stable"):
        path = shutil.which(name)
        if path:
            return path
    import glob as _glob
    import re as _re
    # Playwright ships chrome-linux64/ on current releases, chrome-linux/ on
    # older ones, so match both.
    candidates = []
    for pattern in (
        os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome"),
        os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome"),
        "/usr/lib/chromium/chrome",
        "/opt/google/chrome/chrome",
    ):
        candidates.extend(_glob.glob(pattern))
    if candidates:
        # Sort by the numeric build id so chromium-999 beats chromium-1243
        # instead of losing to plain lexicographic order.
        def _build(path):
            m = _re.search(r"chromium-(\d+)", path)
            return int(m.group(1)) if m else -1

        return max(candidates, key=_build)
    return ""


_WPC_BROWSER_PATH = _find_browser()
_WPC_BROWSER_ARGS = {"no_sandbox": True}
_POT_PROVIDERS = ["wpc"]


def _wpc_extractor_args():
    """WPC extractor args dict, or None when no browser is available.

    yt-dlp's Python API wants every extractor-arg value as a list of strings
    (the CLI joins them with commas); a bare string reaches the provider as
    an empty config — nodriver then logged `executable :/` and every PO Token
    mint failed with "Permission denied".
    """
    if not _WPC_BROWSER_PATH:
        return None
    return {
        "browser_path": [_WPC_BROWSER_PATH],
        "no_sandbox": ["True"],
    }


def _pot_args():
    """Return PO Token provider CLI args (empty when browser unavailable)."""
    if not _WPC_BROWSER_PATH:
        return []
    args = []
    for provider in _POT_PROVIDERS:
        args.extend(["--extractor-args", f"youtubepot-wpc:browser_path={_WPC_BROWSER_PATH}"])
        for key, val in _WPC_BROWSER_ARGS.items():
            args.extend(["--extractor-args", f"youtubepot-wpc:{key}={val}"])
    return args


def _proxy_reachable(timeout: float = 1.5) -> bool:
    """True when the configured proxy is actually accepting connections.

    A hardcoded PROXY_URL turns a WARP outage into a total YouTube outage,
    because every extract and download would fail to connect. Probing the
    listener first lets playback fall back to a direct connection instead.
    """
    if not PROXY:
        return False
    try:
        from urllib.parse import urlparse

        parsed = urlparse(PROXY if "//" in PROXY else f"//{PROXY}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (1080 if parsed.scheme == "socks5" else 80)
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _proxy_args():
    """Return proxy CLI args list, or empty list if no proxy is usable."""
    if not _proxy_reachable():
        return []
    return ["--proxy", PROXY]


def _proxy_dict():
    """Return proxy dict option, or empty dict if no proxy is usable."""
    if not _proxy_reachable():
        return {}
    return {"proxy": PROXY}


def _cookie_args():
    """Return --cookies CLI args when a valid Netscape cookie file exists."""
    path = cookies()
    if not path:
        return []
    return ["--cookies", path]


def _cookie_dict():
    """Return cookiefile dict option when cookies are available."""
    path = cookies()
    if not path:
        return {}
    return {"cookiefile": path}


def _aria2_proxy_args():
    """Return aria2c proxy args list, or empty list if no proxy configured."""
    if not PROXY:
        return []
    return [f"--all-proxy={PROXY}"]


_YT_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


def _base_ydl_opts(**extra):
    """Common yt-dlp options: proxy + cookies + multi-client SABR-safe fallback + PO Token."""
    extractor_args = {"youtube": {"player_client": list(_YT_CLIENTS)}}
    # Add WPC PO Token provider when a browser is available
    wpc = _wpc_extractor_args()
    if wpc:
        extractor_args["youtubepot-wpc"] = wpc
    opts = {
        "extractor_args": extractor_args,
        **_proxy_dict(),
        **_cookie_dict(),
        "geo_bypass": True,
        "noplaylist": True,
        "nocheckcertificate": True,
        "quiet": True,
        "no_warnings": True,
        "prefer_ffmpeg": True,
        "no_overwrites": True,
        "remote_components": ["ejs:github"],
        **_js_runtime_opt(),
        "extractor_retries": 5,
        "fragment_retries": 5,
        "retries": 5,
        "concurrent_fragment_downloads": 5,
        # Both must be ints (bytes), not filesize strings. yt-dlp only runs
        # validate_bytes() on its CLI path; the Python API hands these straight
        # to urllib3/HTTP code, where a str raises
        # "can't multiply sequence by non-int of type 'float'" or
        # "'<' not supported between instances of 'str' and 'int'".
        # This stayed hidden while aria2c was the external downloader because
        # aria2c bypasses the internal HTTP downloader entirely.
        "buffersize": 1024 * 1024,
        "http_chunk_size": 10 * 1024 * 1024,
        "http_headers": _YT_HTTP_HEADERS,
        # NOTE: an aria2c external downloader was configured here with
        # "-x 16 -s 16 -k 1M". Measured on this host it was ~2.3x SLOWER than
        # yt-dlp's native downloader (17.8s vs 7.7s for an 11 MiB audio file):
        # aria2c's startup cost and 16-way splitting outweigh any gain, because
        # googlevideo already streams a single progressive file at full speed.
        # The real per-track cost is YouTube's mandated pre-download sleep
        # (5-6s) plus JS challenge solving, neither of which a downloader helps.
        # Keys are omitted entirely rather than set to "default", which is not
        # a valid external_downloader value and made extract_info return None.
        "postprocessor_args": {
            "ffmpeg": ["-threads", "4", "-preset", "slow", "-q:a", "0"],
        },
    }
    opts.update(extra)
    return opts


def get_available_runtimes():
    return [rt for rt in RUNTIME_PRIORITY if shutil.which(rt)]


def _js_runtime_args():
    """CLI form of the JS runtime flag.

    YouTube's "n challenge" solver (downloaded via --remote-components
    ejs:github) is inert without a runtime to execute it. Passing
    --remote-components alone yields "n challenge solving failed" and NO
    formats, so the runtime is mandatory, not optional.
    """
    runtimes = get_available_runtimes()
    return ["--js-runtimes", runtimes[0]] if runtimes else []


def _js_runtime_opt():
    """dict form of the JS runtime option for YoutubeDL opts.

    NOTE: the Python API wants {runtime: {config}}, NOT a list — passing a list
    raises "Invalid js_runtimes format, expected a dict of {runtime: {config}}".
    """
    runtimes = get_available_runtimes()
    return {"js_runtimes": {runtimes[0]: {}}} if runtimes else {}


def extract_info_with_fallback(link, opts):
    """Try JS runtimes, then progressive format 18 if adaptive formats fail (SABR)."""
    runtimes = get_available_runtimes() or [None]
    last_error = None
    attempts = []
    for runtime in runtimes:
        temp_opts = opts.copy()
        if runtime:
            temp_opts["js_runtime"] = runtime
        attempts.append((runtime or "default", temp_opts))
    # SABR-safe progressive fallback
    fallback_opts = opts.copy()
    fallback_opts["format"] = "18/best/worst"
    if runtimes and runtimes[0]:
        fallback_opts["js_runtime"] = runtimes[0]
    attempts.append(("progressive-18", fallback_opts))

    for label, temp_opts in attempts:
        try:
            _log("info", "extract_info_with_fallback: trying %s", label)
            with _get_youtube_dl()(temp_opts) as ydl:
                return ydl.extract_info(link)
        except Exception as e:
            _log("warning", "extract_info_with_fallback: %s failed: %s", label, e)
            last_error = e
            continue
    raise Exception(f"All extract attempts failed: {last_error}")


NOTHING = {"cookies_dead": None}


async def shell_cmd(cmd):
    _log("debug", "shell_cmd: %s", cmd[:200])
    t0 = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, errorz = await asyncio.wait_for(
            proc.communicate(), timeout=_YT_SUBPROCESS_TIMEOUT
        )
        elapsed = time.monotonic() - t0
        _log("debug", "shell_cmd completed in %.1fs", elapsed)
        if errorz:
            if "unavailable videos are hidden" in (errorz.decode("utf-8")).lower():
                return out.decode("utf-8")
            else:
                return errorz.decode("utf-8")
        return out.decode("utf-8")
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - t0
        _log("error", "shell_cmd TIMEOUT after %.1fs: %s", elapsed, cmd[:100])
        try:
            proc.kill()
        except Exception:
            pass
        try:
            import asyncio as _aio
            _aio.get_running_loop().create_task(
                notify_owner("Youtube.shell_cmd", Exception(f"shell subprocess timed out after {elapsed:.0f}s"), f"cmd={cmd[:120]}")
            )
        except Exception:
            pass
        return ""
    except Exception as e:
        _log("error", "shell_cmd failed: %s", e)
        return ""


class YouTube:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.regex = r"(?:youtube\.com|youtu\.be)"
        self.status = "https://www.youtube.com/oembed?url="
        self.listbase = "https://youtube.com/playlist?list="
        self.reg = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
        # Short-TTL cache & per-track lock for Aoi Hosting Music API URLs so
        # fallback (stream -> download) and prefetch never double/triple-charge quota.
        self._media_url_cache: dict[tuple[str, str], tuple[str, float]] = {}
        self._media_url_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._download_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._http_session: aiohttp.ClientSession | None = None

    async def exists(self, link: str, videoid: bool | str = None):
        if videoid:
            link = self.base + link
        if re.search(self.regex, link):
            return True
        else:
            return False

    @property
    def use_fallback(self):
        return NOTHING["cookies_dead"] is True

    @use_fallback.setter
    def use_fallback(self, value):
        if NOTHING["cookies_dead"] is None:
            NOTHING["cookies_dead"] = value

    async def url(self, message_1: Message) -> str | None:
        messages = [message_1]
        if message_1.reply_to_message:
            messages.append(message_1.reply_to_message)
        text = ""
        offset = None
        length = None
        for message in messages:
            if offset:
                break
            if message.entities:
                for entity in message.entities:
                    if entity.type == MessageEntityType.URL:
                        text = message.text or message.caption
                        offset, length = entity.offset, entity.length
                        break
            elif message.caption_entities:
                for entity in message.caption_entities:
                    if entity.type == MessageEntityType.TEXT_LINK:
                        return entity.url
        if offset in (None,):
            return None
        return text[offset : offset + length]

    @alru_cache(maxsize=128)
    async def details(self, link: str, videoid: bool | str = None):
        _log("info", "details() link=%s videoid=%s", link[:80], videoid)
        t0 = time.monotonic()
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        try:
            results = VideosSearch(link, limit=1, timeout=_YT_SEARCH_TIMEOUT)
            search_result = await asyncio.wait_for(
                results.next(), timeout=_YT_SEARCH_TIMEOUT + 5
            )
            items = search_result.get("result", [])
            if not items:
                _log("warning", "details() empty results for: %s", link[:80])
                raise Exception("no search results")
            result = items[0]
            title = result["title"]
            duration_min = result["duration"]
            thumbnail = result["thumbnails"][0]["url"].split("?")[0]
            vidid = result["id"]
            if str(duration_min) == "None":
                duration_sec = 0
            else:
                duration_sec = int(time_to_seconds(duration_min))
            _log(
                "info",
                "details() OK in %.1fs title=%s vidid=%s",
                time.monotonic() - t0,
                title[:40],
                vidid,
            )
            return title, duration_min, duration_sec, thumbnail, vidid
        except asyncio.TimeoutError:
            _log(
                "error",
                "details() TIMEOUT after %.1fs for: %s",
                time.monotonic() - t0,
                link[:80],
            )
            raise
        except Exception as e:
            _log(
                "error",
                "details() FAILED after %.1fs for: %s err=%s",
                time.monotonic() - t0,
                link[:80],
                e,
            )
            raise

    @alru_cache(maxsize=64)
    async def title(self, link: str, videoid: bool | str = None):
        _log("info", "title() link=%s videoid=%s", link[:80], videoid)
        t0 = time.monotonic()
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        try:
            results = VideosSearch(link, limit=1, timeout=_YT_SEARCH_TIMEOUT)
            search_result = await asyncio.wait_for(
                results.next(), timeout=_YT_SEARCH_TIMEOUT + 5
            )
            items = search_result.get("result", [])
            if not items:
                raise Exception("no search results")
            title = items[0]["title"]
            _log("info", "title() OK in %.1fs: %s", time.monotonic() - t0, title[:50])
            return title
        except asyncio.TimeoutError:
            _log("error", "title() TIMEOUT after %.1fs for: %s", time.monotonic() - t0, link[:80])
            raise
        except Exception as e:
            _log("error", "title() FAILED for: %s err=%s", link[:80], e)
            raise

    @alru_cache(maxsize=64)
    async def duration(self, link: str, videoid: bool | str = None):
        _log("info", "duration() link=%s videoid=%s", link[:80], videoid)
        t0 = time.monotonic()
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        try:
            results = VideosSearch(link, limit=1, timeout=_YT_SEARCH_TIMEOUT)
            search_result = await asyncio.wait_for(
                results.next(), timeout=_YT_SEARCH_TIMEOUT + 5
            )
            items = search_result.get("result", [])
            if not items:
                raise Exception("no search results")
            duration = items[0]["duration"]
            _log("info", "duration() OK in %.1fs: %s", time.monotonic() - t0, duration)
            return duration
        except asyncio.TimeoutError:
            _log("error", "duration() TIMEOUT for: %s", link[:80])
            raise
        except Exception as e:
            _log("error", "duration() FAILED for: %s err=%s", link[:80], e)
            raise

    @alru_cache(maxsize=64)
    async def thumbnail(self, link: str, videoid: bool | str = None):
        _log("info", "thumbnail() link=%s videoid=%s", link[:80], videoid)
        t0 = time.monotonic()
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        try:
            results = VideosSearch(link, limit=1, timeout=_YT_SEARCH_TIMEOUT)
            search_result = await asyncio.wait_for(
                results.next(), timeout=_YT_SEARCH_TIMEOUT + 5
            )
            items = search_result.get("result", [])
            if not items:
                raise Exception("no search results")
            thumbnail = items[0]["thumbnails"][0]["url"].split("?")[0]
            _log("info", "thumbnail() OK in %.1fs", time.monotonic() - t0)
            return thumbnail
        except asyncio.TimeoutError:
            _log("error", "thumbnail() TIMEOUT for: %s", link[:80])
            raise
        except Exception as e:
            _log("error", "thumbnail() FAILED for: %s err=%s", link[:80], e)
            raise

    async def video(self, link: str, videoid: bool | str = None):
        _log("info", "video() link=%s videoid=%s", link[:80], videoid)
        t0 = time.monotonic()
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        cmd = [
            yt_dlp_binary(),
            *_proxy_args(),
            *_cookie_args(),
            "-g",
            "-f",
            _VIDEO_FMT,
            "--extractor-args", f"youtube:player_client={_YT_CLIENTS_STR}",
            *_pot_args(),
            "--remote-components", "ejs:github",
            f"{link}",
        ]
        _log("info", "video() subprocess: %s", " ".join(cmd[:6]))
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_YT_SUBPROCESS_TIMEOUT
            )
            elapsed = time.monotonic() - t0
            if stdout:
                url = stdout.decode().split("\n")[0]
                _log("info", "video() OK in %.1fs url_len=%d", elapsed, len(url))
                return 1, url
            else:
                err = stderr.decode()[:200]
                _log("error", "video() FAILED in %.1fs stderr=%s", elapsed, err)
                return 0, err
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - t0
            _log("error", "video() TIMEOUT after %.1fs for: %s", elapsed, link[:80])
            try:
                proc.kill()
            except Exception:
                pass
            try:
                import asyncio as _aio
                _aio.get_running_loop().create_task(
                    notify_owner("Youtube.video", Exception(f"yt-dlp subprocess timed out after {elapsed:.0f}s"), f"link={link[:80]}")
                )
            except Exception:
                pass
            return 0, "timeout"
        except Exception as e:
            _log("error", "video() EXCEPTION: %s", e)
            return 0, str(e)

    async def _streamable(self, url: str) -> bool:
        """True if the direct URL can be pulled (uses proxy when configured)."""
        try:
            proxies = PROXY if PROXY else None
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=_YT_STREAM_CHECK_TIMEOUT,
                proxy=proxies,
                headers=_YT_HTTP_HEADERS,
            ) as client:
                resp = await client.get(url, headers={"Range": "bytes=0-1023"})
            return resp.status_code in (200, 206)
        except Exception:
            # Don't block streaming if probe fails — ffmpeg often still works
            return True

    @staticmethod
    async def _url_reachable(url: str, timeout: float = 8.0):
        """Verify a googlevideo URL is actually playable by ffmpeg.

        yt-dlp -g can hand back a URL that extracts fine but is rejected with
        HTTP 403 at playback time. Returning n=1 in that case makes the bot
        join the voice chat in silence, so probe it and let the caller fall
        back to a real download.
        """
        headers = dict(_YT_HTTP_HEADERS)
        headers["Range"] = "bytes=0-1023"
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                headers=headers,
                proxy=PROXY or None,
            ) as client:
                resp = await client.get(url)
            if resp.status_code in (200, 206):
                return True, ""
            return False, f"http_{resp.status_code}"
        except Exception as e:
            return False, type(e).__name__

    @staticmethod
    def _extract_video_id(link: str) -> str | None:
        """Extract an 11-character YouTube video ID from a URL or raw ID."""
        if not link:
            return None
        s = str(link).strip()
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
            return s
        patterns = (
            r"(?:v=|/v/|/embed/|/shorts/|youtu\.be/)([A-Za-z0-9_-]{11})",
        )
        for pat in patterns:
            m = re.search(pat, s)
            if m:
                return m.group(1)
        return None

    async def _get_http_session(self) -> aiohttp.ClientSession:
        """Return a shared keep-alive aiohttp session (avoids per-request TLS handshakes)."""
        if self._http_session is None or self._http_session.closed:
            connector = aiohttp.TCPConnector(
                limit=64,
                ttl_dns_cache=300,
                keepalive_timeout=60,
            )
            self._http_session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=120, connect=10, sock_read=60),
            )
        return self._http_session

    async def get_media_url(self, video_id: str, media_type: str = "audio") -> str:
        """Fetch a unique MP3/MP4 stream URL from the Aoi Hosting Music API.

        Endpoints:
            GET {MEDIA_API_URL}/api/audio/{video_id}  (MP3)
            GET {MEDIA_API_URL}/api/video/{video_id}  (MP4)
        Auth: X-API-Key header (MEDIA_API_KEY).

        Results are cached in-memory for a short TTL (up to 5 minutes, bounded
        by the backend's expires_in) and deduplicated per (video_id, media_type)
        so fallback chains (stream -> download) and prefetch reuse the same URL
        instead of consuming multiple API quota units for a single play.
        """
        api_base = (getattr(config, "MEDIA_API_URL", "") or "https://host-repository.vercel.app").strip().rstrip("/")
        api_key = (getattr(config, "MEDIA_API_KEY", "") or "").strip()
        if not api_base or not api_key:
            raise RuntimeError("MEDIA_API_URL or MEDIA_API_KEY is not configured")

        vid = self._extract_video_id(video_id) or str(video_id).strip()
        mtype = "video" if str(media_type).lower() == "video" else "audio"
        cache_key = (vid, mtype)

        now = time.monotonic()
        cached = self._media_url_cache.get(cache_key)
        if cached and cached[1] > now:
            return cached[0]

        lock = self._media_url_locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            cached = self._media_url_cache.get(cache_key)
            if cached and cached[1] > now:
                return cached[0]

            if len(self._media_url_cache) > 128:
                self._media_url_cache = {
                    k: v for k, v in self._media_url_cache.items() if v[1] > now
                }
                for k in list(self._media_url_locks.keys()):
                    if (
                        k != cache_key
                        and not self._media_url_locks[k].locked()
                        and k not in self._media_url_cache
                    ):
                        self._media_url_locks.pop(k, None)

            url = f"{api_base}/api/{mtype}/{vid}"

            session = await self._get_http_session()
            last_err = None
            for attempt in range(2):
                req_t0 = time.monotonic()
                try:
                    async with session.get(
                        url,
                        headers={"X-API-Key": api_key},
                        timeout=aiohttp.ClientTimeout(total=58),
                    ) as resp:
                        if resp.status == 401:
                            raise RuntimeError("Invalid or missing Music API key (HTTP 401)")
                        if resp.status == 402:
                            raise RuntimeError("Music API balance below 0.001 Coins (HTTP 402)")
                        if resp.status == 429:
                            raise RuntimeError("Music API daily free limit reached (HTTP 429)")
                        if resp.status in (502, 503, 504):
                            last_err = RuntimeError(
                                f"Music API backend unavailable (HTTP {resp.status}, auto-refunded)"
                            )
                            if attempt == 0 and (time.monotonic() - req_t0) < 15.0:
                                await asyncio.sleep(0.5)
                                continue
                            raise last_err
                        data = await resp.json(content_type=None)
                        if not isinstance(data, dict) or not data.get("ok") or not data.get("url"):
                            raise RuntimeError(
                                (data.get("error") if isinstance(data, dict) else None)
                                or f"Music API unexpected response (HTTP {resp.status})"
                            )
                        stream_url = str(data["url"])
                        try:
                            expires_in = int(data.get("expires_in") or 300)
                        except (TypeError, ValueError):
                            expires_in = 300
                        ttl = max(30, min(expires_in - 15, 300))
                        self._media_url_cache[cache_key] = (stream_url, time.monotonic() + ttl)
                        return stream_url
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    last_err = exc
                    if attempt == 0 and (time.monotonic() - req_t0) < 15.0:
                        await asyncio.sleep(0.5)
                        continue
                    raise
            raise last_err or RuntimeError("Music API request failed")

    def invalidate_media_url(self, video_id: str, media_type: str = "audio") -> None:
        """Evict a stale or broken stream URL from the in-memory cache."""
        vid = self._extract_video_id(video_id) or str(video_id).strip()
        mtype = "video" if str(media_type).lower() == "video" else "audio"
        self._media_url_cache.pop((vid, mtype), None)

    async def _api_download(self, link: str, media_type: str = "audio") -> str | None:
        """Download MP3/MP4 directly via the Aoi Hosting Music API into downloads/.

        Uses a persistent keep-alive session and 1-hop ?redirect=1 download when
        no cached stream URL is present, matching ~230ms curl throughput.
        """
        api_key = (getattr(config, "MEDIA_API_KEY", "") or "").strip()
        if not api_key:
            return None
        vid = self._extract_video_id(link)
        if not vid:
            return None
        mtype = "video" if str(media_type).lower() == "video" else "audio"
        ext = "mp4" if mtype == "video" else "mp3"
        os.makedirs("downloads", exist_ok=True)
        out_path = os.path.join("downloads", f"{vid}.{ext}")
        if os.path.exists(out_path) and os.path.getsize(out_path) >= 10240:
            return out_path

        cache_key = (vid, mtype)
        lock = self._download_locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            if os.path.exists(out_path) and os.path.getsize(out_path) >= 10240:
                return out_path
            part_path = f"{out_path}.part"
            try:
                session = await self._get_http_session()
                api_base = (getattr(config, "MEDIA_API_URL", "") or "https://host-repository.vercel.app").strip().rstrip("/")
                for attempt in range(2):
                    now = time.monotonic()
                    cached = self._media_url_cache.get(cache_key)
                    used_cache = bool(cached and cached[1] > now)
                    if used_cache:
                        req_url = cached[0]
                        req_headers = {}
                    else:
                        req_url = f"{api_base}/api/{mtype}/{vid}?redirect=1"
                        req_headers = {"X-API-Key": api_key}

                    async with session.get(
                        req_url,
                        headers=req_headers,
                        allow_redirects=True,
                        timeout=aiohttp.ClientTimeout(total=120),
                    ) as resp:
                        if resp.status not in (200, 206):
                            if resp.status >= 400:
                                self._media_url_cache.pop(cache_key, None)
                            _log("warning", "Media API file fetch HTTP %s for %s (cached=%s)", resp.status, vid, used_cache)
                            if used_cache and attempt == 0:
                                continue
                            return None
                        # Cache the final redirected URL for any subsequent reuse
                        final_url = str(resp.url)
                        if final_url and final_url != req_url:
                            self._media_url_cache[cache_key] = (final_url, time.monotonic() + 240)
                        with open(part_path, "wb") as f:
                            async for chunk in resp.content.iter_chunked(131072):
                                if chunk:
                                    f.write(chunk)
                    if os.path.exists(part_path) and os.path.getsize(part_path) >= 10240:
                        os.replace(part_path, out_path)
                        _log("info", "Media API downloaded %s -> %s", vid, out_path)
                        return out_path
                    if used_cache and attempt == 0:
                        self._media_url_cache.pop(cache_key, None)
                        continue
                    break
                try:
                    if os.path.exists(part_path):
                        os.remove(part_path)
                except Exception:
                    pass
            except Exception as e:
                _log("warning", "Media API download fallback (%s): %s", vid, e)
                try:
                    if os.path.exists(part_path):
                        os.remove(part_path)
                except Exception:
                    pass
            return None

    async def stream_url(
        self,
        link: str,
        videoid: bool | str = None,
        video: bool = False,
        use_api: bool = True,
    ):
        """Get a direct playable URL (no full download). Fast path for VC play."""
        _log("info", "stream_url() link=%s videoid=%s video=%s", link[:80], videoid, video)
        t0 = time.monotonic()
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        # Primary fast-path: Aoi Hosting Music API (/api/audio/{id} or /api/video/{id})
        if use_api and (getattr(config, "MEDIA_API_KEY", "") or "").strip():
            vid = self._extract_video_id(link)
            if vid:
                try:
                    api_url = await self.get_media_url(vid, media_type="video" if video else "audio")
                    _log("info", "stream_url() OK via Media API in %.1fs (%s)", time.monotonic() - t0, vid)
                    return 1, api_url
                except Exception as e:
                    _log("warning", "stream_url() Media API failed for %s: %s (falling back to yt-dlp)", vid, e)
        fmt = _VIDEO_FMT if video else _AUDIO_FMT
        cmd = [
            yt_dlp_binary(),
            *_proxy_args(),
            *_cookie_args(),
            "-g",
            "-f",
            fmt,
            "--no-playlist",
            "--extractor-args", f"youtube:player_client={_YT_CLIENTS_STR}",
            *_pot_args(),
            "--remote-components", "ejs:github",
            *_js_runtime_args(),
            "--user-agent", _YT_HTTP_HEADERS["User-Agent"],
            f"{link}",
        ]
        _log("info", "stream_url() subprocess: %s", " ".join(cmd[:10]))
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_YT_SUBPROCESS_TIMEOUT
            )
            elapsed = time.monotonic() - t0
            if stdout:
                # yt-dlp -g may print multiple lines (video+audio); take first http URL
                lines = [ln.strip() for ln in stdout.decode().splitlines() if ln.strip()]
                url = next((ln for ln in lines if ln.startswith("http")), lines[0] if lines else "")
                if not url:
                    _log("error", "stream_url() empty url in %.1fs", elapsed)
                    return 0, "empty url"
                ok, why = await self._url_reachable(url)
                if not ok:
                    _log("error", "stream_url() direct URL unusable (%s) in %.1fs -> forcing download fallback", why, elapsed)
                    return 0, f"direct url {why}"
                _log("info", "stream_url() got direct URL in %.1fs (skip full download)", elapsed)
                return 1, url
            else:
                err = stderr.decode()[:200]
                _log("error", "stream_url() FAILED in %.1fs stderr=%s", elapsed, err)
                return 0, err
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - t0
            _log("error", "stream_url() TIMEOUT after %.1fs for: %s", elapsed, link[:80])
            try:
                proc.kill()
            except Exception:
                pass
            try:
                import asyncio as _aio
                _aio.get_running_loop().create_task(
                    notify_owner("Youtube.stream_url", Exception(f"yt-dlp subprocess timed out after {elapsed:.0f}s"), f"link={link[:80]} video={video}")
                )
            except Exception:
                pass
            return 0, "timeout"
        except Exception as e:
            _log("error", "stream_url() EXCEPTION: %s", e)
            return 0, str(e)

    @alru_cache(maxsize=64)
    async def playlist(self, link, limit, videoid: bool | str = None):
        _log("info", "playlist() link=%s limit=%s videoid=%s", link[:80], limit, videoid)
        t0 = time.monotonic()
        if videoid:
            link = self.listbase + link
        match = re.search(r"[?&]list=([\w-]+)", link)
        if not match:
            match = re.search(r"youtube\.com/playlist\?([\w-]+)", link)
        if match:
            link = f"https://www.youtube.com/playlist?list={match.group(1)}"
        if "&" in link:
            link = link.split("&")[0]

        list_id = str(match.group(1)) if match else None
        if list_id:
            api_result = await self._api_playlist(list_id, limit)
            if api_result:
                return api_result

        proxy_part = f"--proxy {shlex.quote(PROXY)} " if PROXY else ""
        cookie_path = cookies()
        cookie_part = f"--cookies {shlex.quote(cookie_path)} " if cookie_path else ""
        pot_part = f"--extractor-args 'youtubepot-wpc:browser_path={_WPC_BROWSER_PATH}' " if _WPC_BROWSER_PATH else ""
        cmd = (
            f"{shlex.quote(yt_dlp_binary())} {proxy_part}{cookie_part}{pot_part}"
            f"-i --compat-options no-youtube-unavailable-videos "
            f"--extractor-args 'youtube:player_client={_YT_CLIENTS_STR}' "
            f'--get-id --flat-playlist --playlist-end {limit} --skip-download "{link}" '
            f"2>/dev/null"
        )

        playlist = await shell_cmd(cmd)

        try:
            result = [key for key in playlist.split("\n") if key]
        except Exception:
            result = []

        # Radio/Mix lists (RD...) are not real playlist pages — yt-dlp comes
        # back with zero entries because the page is JS-driven. YouTube's
        # InnerTube "next" endpoint enumerates mixes reliably (this is what
        # other players do); a watch-page retry is kept as backup.
        if not result and match and str(match.group(1)).startswith("RD"):
            plist_id = str(match.group(1))
            _log("info", "playlist() empty for mix %s — trying InnerTube next", plist_id)
            result = await self._mix_tracks(plist_id, limit)
            if not result:
                watch_link = f"https://www.youtube.com/watch?list={plist_id}"
                _log("info", "playlist() mix retry via watch page %s", plist_id)
                mix_cmd = (
                    f"{shlex.quote(yt_dlp_binary())} {proxy_part}{cookie_part}{pot_part}"
                    f"-i --extractor-args 'youtube:player_client=android,tv'"
                    f' --get-id --flat-playlist --playlist-end {limit} --skip-download "{watch_link}" '
                    f"2>/dev/null"
                )
                mix_raw = await shell_cmd(mix_cmd)
                try:
                    result = [key for key in mix_raw.split("\n") if key]
                except Exception:
                    result = []
            _log("info", "playlist() mix fallback got %d items in %.1fs", len(result), time.monotonic() - t0)

        _log("info", "playlist() OK in %.1fs got %d items", time.monotonic() - t0, len(result))
        return result

    async def _mix_tracks(self, plist_id: str, limit: int) -> list:
        """Enumerate a YouTube Mix/radio playlist through the InnerTube next API.

        Mixes never render as playlist pages, so yt-dlp finds nothing; the
        `next` endpoint with just the playlistId returns the queue panel.
        """
        payload = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20260101.00.00",
                    "hl": "en",
                }
            },
            "playlistId": plist_id,
        }
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=12)
            ) as session:
                async with session.post(
                    _INNERTUBE_NEXT_URL,
                    json=payload,
                    headers={"content-type": "application/json"},
                ) as resp:
                    data = await resp.json(content_type=None)
        except Exception as e:
            _log("warning", "playlist() mix API failed: %s", e)
            return []

        ids: list = []

        def _walk(node):
            if isinstance(node, dict):
                panel = node.get("playlistPanelVideoRenderer")
                if isinstance(panel, dict) and panel.get("videoId"):
                    ids.append(panel["videoId"])
                for value in node.values():
                    _walk(value)
            elif isinstance(node, list):
                for value in node:
                    _walk(value)

        _walk(data)
        return ids[:limit]

    async def _api_playlist(self, list_id: str, limit: int) -> list:
        """Enumerate a playlist through the media API's walker (fast path).

        The API already holds a signed-in session and its own pot tokens;
        its /v1/playlist endpoints answer from a cached snapshot, and it walks
        mixes (RD…) too. Ids come back in playlist order, unavailable videos
        dropped. Empty list on any problem — the yt-dlp path below is the
        fallback and must still work.
        """
        url = (getattr(config, "MEDIA_API_URL", "") or "").strip()
        if not url:
            return []
        t0 = time.monotonic()
        headers = {"accept": "application/json"}
        if getattr(config, "MEDIA_API_KEY", ""):
            headers["x-api-key"] = config.MEDIA_API_KEY
        ids: list = []
        cursor = None
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            ) as session:
                while len(ids) < limit:
                    if cursor:
                        payload = {"cursor": cursor,
                                   "limit": min(100, limit - len(ids))}
                        path = "/v1/playlist/items"
                    else:
                        payload = {"id": list_id, "limit": min(100, limit)}
                        path = "/v1/playlist/info"
                    async with session.post(
                        url.rstrip("/") + path,
                        json=payload,
                        headers=headers,
                    ) as resp:
                        if resp.status != 200:
                            _log("debug", "playlist API %s answered %d",
                                 path, resp.status)
                            return []
                        data = await resp.json(content_type=None)
                    if not isinstance(data, dict):
                        return []
                    for item in data.get("videos") or data.get("items") or []:
                        if item.get("id") and item.get("available", True):
                            ids.append(item["id"])
                    pg = data.get("pagination") or {}
                    cursor = pg.get("next_cursor")
                    if not (cursor and pg.get("has_more")):
                        break
        except Exception as e:
            _log("warning", "playlist API failed: %s: %s",
                 type(e).__name__, e)
            return []
        if not ids:
            return []
        _log("info", "playlist() OK via media API in %.1fs got %d items (%s)",
             time.monotonic() - t0, len(ids), list_id)
        return ids[:limit]

    async def _api_search(self, query: str, t0: float):
        """Resolve a search query through the media API (warm, ~0.5s).

        Returns the same shape track() produces, or None to let the caller
        fall back to the local VideosSearch path.
        """
        if getattr(self, "_api_search_disabled", False):
            return None
        url = (getattr(config, "MEDIA_API_URL", "") or "").strip()
        if not url or "host-repository.vercel.app" in url:
            return None
        try:
            _headers = {"accept": "application/json"}
            _key = (getattr(config, "MEDIA_API_KEY", "") or "").strip()
            if _key:
                _headers["x-api-key"] = _key
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=6)
            ) as session:
                async with session.post(
                    f"{url.rstrip('/')}/v1/search",
                    json={"q": query, "limit": 5},
                    headers=_headers,
                ) as resp:
                    if resp.status == 404:
                        # API only serves /api/audio/{id} & /api/video/{id} (no /v1/search).
                        self._api_search_disabled = True
                        return None
                    if resp.status != 200:
                        # A dead/misconfigured API degrades every play into the
                        # slower fallbacks with no user-visible trace: tell the
                        # owner (notify_owner dedups bursts).
                        e = Exception(f"/v1/search returned HTTP {resp.status}")
                        asyncio.create_task(
                            notify_owner(
                                "Media API search",
                                e,
                                f"query={query[:80]} — falling back to local search",
                            )
                        )
                        _log("warning", "media API search HTTP %s: falling back", resp.status)
                        return None
                    payload = await resp.json()
        except Exception as e:
            _log("warning", "media API search failed: %s: %s", type(e).__name__, e)
            asyncio.create_task(
                notify_owner(
                    "Media API search",
                    e,
                    f"query={query[:80]} — falling back to local search (is media-api.service up?)",
                )
            )
            return None
        results = payload.get("results") or []
        if not results:
            return None
        item = results[0]
        vidid = item.get("id")
        if not vidid:
            return None
        duration_sec = item.get("duration") or 0
        track_details = {
            "title": item.get("title") or vidid,
            "link": item.get("url") or f"https://www.youtube.com/watch?v={vidid}",
            "vidid": vidid,
            "duration_min": seconds_to_min(duration_sec),
            "thumb": (item.get("thumbnail") or f"https://i.ytimg.com/vi/{vidid}/hq720.jpg").split("?")[0],
            # Channel name doubles as the artist hint for /v1/lyrics.
            "uploader": item.get("channel"),
        }
        _log(
            "info",
            "track() OK via media API in %.1fs title=%s vidid=%s",
            time.monotonic() - t0,
            track_details["title"][:40],
            vidid,
        )
        return track_details, vidid

    @alru_cache(maxsize=128)
    async def track(self, link: str, videoid: bool | str = None):
        _log("info", "track() link=%s videoid=%s", link[:80], videoid)
        t0 = time.monotonic()
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        if link.startswith("http://") or link.startswith("https://"):
            vid = self._extract_video_id(link)
            if vid:
                try:
                    results = VideosSearch(vid, limit=1, timeout=_YT_SEARCH_TIMEOUT)
                    search_result = await asyncio.wait_for(
                        results.next(), timeout=_YT_SEARCH_TIMEOUT + 5
                    )
                    items = search_result.get("result", [])
                    if items:
                        result = items[0]
                        _chan = result.get("channel")
                        track_details = {
                            "title": result["title"],
                            "link": result.get("link") or f"https://www.youtube.com/watch?v={vid}",
                            "vidid": result.get("id") or vid,
                            "duration_min": result.get("duration"),
                            "thumb": result["thumbnails"][0]["url"].split("?")[0],
                            "uploader": (_chan.get("name") if isinstance(_chan, dict) else _chan),
                        }
                        _log("info", "track() URL OK via VideosSearch in %.1fs vidid=%s", time.monotonic() - t0, track_details["vidid"])
                        return track_details, track_details["vidid"]
                except Exception as url_err:
                    _log("warning", "track() URL VideosSearch failed for %s: %s", vid, url_err)
            _log("info", "track() URL detected, delegating to _track()")
            try:
                return await self._track(link)
            except Exception as track_err:
                if vid:
                    try:
                        session = await self._get_http_session()
                        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json"
                        async with session.get(oembed_url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                            if resp.status == 200:
                                odata = await resp.json(content_type=None)
                                track_details = {
                                    "title": odata.get("title") or vid,
                                    "link": f"https://www.youtube.com/watch?v={vid}",
                                    "vidid": vid,
                                    "duration_min": "00:00",
                                    "thumb": odata.get("thumbnail_url") or f"https://i.ytimg.com/vi/{vid}/hq720.jpg",
                                    "uploader": odata.get("author_name"),
                                }
                                _log("info", "track() URL recovered via oEmbed for %s", vid)
                                return track_details, vid
                    except Exception:
                        pass
                raise track_err
        api_details = await self._api_search(link, t0)
        if api_details:
            return api_details
        try:
            _log("info", "track() search query, trying VideosSearch...")
            # Word-trim ladder: a half-remembered line often has no exact
            # YouTube match, but chopping it down does ("aaye haaye jawani
            # le doobi" -> "jawani le doobi").
            for variant in _query_variants(link):
                results = VideosSearch(variant, limit=1, timeout=_YT_SEARCH_TIMEOUT)
                search_result = await asyncio.wait_for(
                    results.next(), timeout=_YT_SEARCH_TIMEOUT + 5
                )
                items = search_result.get("result", [])
                if items:
                    if variant != link:
                        _log(
                            "info",
                            "track() VideosSearch recovered after trim %r -> %r",
                            link[:60],
                            variant[:60],
                        )
                    break
            else:
                _log("warning", "track() VideosSearch empty for all %d query variants, falling back to _track()", len(_query_variants(link)))
                return await self._track(link)
            result = items[0]
            title = result["title"]
            duration_min = result["duration"]
            vidid = result["id"]
            yturl = result["link"]
            thumbnail = result["thumbnails"][0]["url"].split("?")[0]
            _chan = result.get("channel")
            track_details = {
                "title": title,
                "link": yturl,
                "vidid": vidid,
                "duration_min": duration_min,
                "thumb": thumbnail,
                # Artist hint for /v1/lyrics (py_yt wraps it in a dict).
                "uploader": (_chan.get("name") if isinstance(_chan, dict) else _chan),
            }
            _log(
                "info",
                "track() OK via VideosSearch in %.1fs title=%s vidid=%s",
                time.monotonic() - t0,
                title[:40],
                vidid,
            )
            return track_details, vidid
        except asyncio.TimeoutError:
            _log(
                "error",
                "track() VideosSearch TIMEOUT after %.1fs, falling back to _track()",
                time.monotonic() - t0,
            )
            try:
                return await self._track(link)
            except Exception as e2:
                _log("error", "track() _track() ALSO FAILED: %s", e2)
                try:
                    await notify_owner("Youtube.track", e2, f"query={link[:80]} (VideosSearch timed out, _track also failed)")
                except Exception:
                    pass
                raise
        except Exception as e:
            _log(
                "warning",
                "track() VideosSearch FAILED: %s, falling back to _track()",
                e,
            )
            try:
                return await self._track(link)
            except Exception as e2:
                _log("error", "track() _track() ALSO FAILED: %s", e2)
                try:
                    await notify_owner("Youtube.track", e2, f"query={link[:80]} (VideosSearch error: {e}, _track also failed)")
                except Exception:
                    pass
                raise

    @asyncify
    def _track(self, q):
        _log("info", "_track() ytsearch: %s", q[:80])
        t0 = time.monotonic()
        extractor_args = {"youtube": {"player_client": list(_YT_CLIENTS)}}
        wpc = _wpc_extractor_args()
        if wpc:
            extractor_args["youtubepot-wpc"] = wpc
        options = {
            "format": _AUDIO_FMT,
            "noplaylist": True,
            "quiet": True,
            "extract_flat": "in_playlist",
            "extractor_args": extractor_args,
            **_proxy_dict(),
            **_cookie_dict(),
            "remote_components": ["ejs:github"],
        }
        try:
            with _get_youtube_dl()(options) as ydl:
                info_dict = None
                details = None
                if q.startswith("http://") or q.startswith("https://"):
                    info_dict = ydl.extract_info(q, download=False)
                    if info_dict:
                        details = info_dict["entries"][0] if info_dict.get("entries") else info_dict
                else:
                    variants = _query_variants(q)
                    for variant in variants:
                        info_dict = ydl.extract_info(f"ytsearch: {variant}", download=False)
                        if info_dict and info_dict.get("entries"):
                            if variant != q:
                                _log(
                                    "info",
                                    "_track() recovered after trim %r -> %r",
                                    q[:60],
                                    variant[:60],
                                )
                            details = info_dict["entries"][0]
                            break
                elapsed = time.monotonic() - t0
                if not details or not details.get("id"):
                    _log("error", "_track() no entries in %.1fs for: %s", elapsed, q[:80])
                    raise Exception("no entries from ytsearch")
                thumbs = details.get("thumbnails") or []
                thumb_url = (
                    thumbs[0]["url"]
                    if thumbs and isinstance(thumbs[0], dict) and thumbs[0].get("url")
                    else (details.get("thumbnail") or f"https://i.ytimg.com/vi/{details['id']}/hq720.jpg")
                )
                dur = details.get("duration") or 0
                info = {
                    "title": details.get("title") or details["id"],
                    "link": details.get("webpage_url") or details.get("url") or f"https://www.youtube.com/watch?v={details['id']}",
                    "vidid": details["id"],
                    "duration_min": (
                        seconds_to_min(dur)
                        if dur != 0
                        else None
                    ),
                    "thumb": thumb_url,
                    # Artist hint for /v1/lyrics.
                    "uploader": details.get("uploader") or details.get("channel"),
                }
                _log(
                    "info",
                    "_track() OK in %.1fs title=%s vidid=%s",
                    elapsed,
                    info["title"][:40],
                    info["vidid"],
                )
                return info, details["id"]
        except Exception as e:
            elapsed = time.monotonic() - t0
            _log("error", "_track() FAILED after %.1fs for: %s err=%s", elapsed, q[:80], e)
            try:
                import asyncio as _aio
                _aio.get_running_loop().create_task(
                    notify_owner("Youtube._track(yt-dlp)", e, f"query={q[:80]} elapsed={elapsed:.1f}s")
                )
            except Exception:
                pass
            raise

    @alru_cache(maxsize=64)
    @asyncify
    def formats(self, link: str, videoid: bool | str = None):
        _log("info", "formats() link=%s videoid=%s", link[:80], videoid)
        t0 = time.monotonic()
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]

        ytdl_opts = {
            "quiet": True,
            "extractor_args": {"youtube": {"player_client": list(_YT_CLIENTS)}},
            **_proxy_dict(),
            **_cookie_dict(),
            "remote_components": ["ejs:github"],
            "extractor_retries": 5,
            "fragment_retries": 5,
            "retries": 5,
        }
        wpc = _wpc_extractor_args()
        if wpc:
            ytdl_opts["extractor_args"]["youtubepot-wpc"] = wpc

        ydl = _get_youtube_dl()(ytdl_opts)
        with ydl:
            formats_available = []
            r = ydl.extract_info(link, download=False)
            for format in r["formats"]:
                try:
                    str(format["format"])
                except Exception:
                    continue
                if "dash" not in str(format["format"]).lower():
                    try:
                        format["format"]
                        format["filesize"]
                        format["format_id"]
                        format["ext"]
                        format["format_note"]
                    except KeyError:
                        continue
                    formats_available.append(
                        {
                            "format": format["format"],
                            "filesize": format["filesize"],
                            "format_id": format["format_id"],
                            "ext": format["ext"],
                            "format_note": format["format_note"],
                            "yturl": link,
                        }
                    )
        _log("info", "formats() OK in %.1fs got %d formats", time.monotonic() - t0, len(formats_available))
        return formats_available, link

    @alru_cache(maxsize=64)
    async def slider(
        self,
        link: str,
        query_type: int,
        videoid: bool | str = None,
    ):
        _log("info", "slider() link=%s query_type=%s videoid=%s", link[:80], query_type, videoid)
        t0 = time.monotonic()
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        try:
            a = VideosSearch(link, limit=10, timeout=_YT_SEARCH_TIMEOUT)
            search_result = await asyncio.wait_for(
                a.next(), timeout=_YT_SEARCH_TIMEOUT + 5
            )
            result = search_result.get("result")
            if not result or len(result) <= query_type:
                raise Exception("insufficient search results for slider")
            title = result[query_type]["title"]
            duration_min = result[query_type]["duration"]
            vidid = result[query_type]["id"]
            thumbnail = result[query_type]["thumbnails"][0]["url"].split("?")[0]
            _log("info", "slider() OK in %.1fs title=%s", time.monotonic() - t0, title[:40])
            return title, duration_min, thumbnail, vidid
        except asyncio.TimeoutError:
            _log("error", "slider() TIMEOUT after %.1fs", time.monotonic() - t0)
            raise
        except Exception as e:
            _log("error", "slider() FAILED: %s", e)
            raise

    async def download(
        self,
        link: str,
        mystic,
        video: bool | str = None,
        videoid: bool | str = None,
        songaudio: bool | str = None,
        songvideo: bool | str = None,
        format_id: bool | str = None,
        title: bool | str = None,
    ) -> str:
        _log(
            "info",
            "download() link=%s videoid=%s video=%s songaudio=%s songvideo=%s format_id=%s",
            link[:80],
            videoid,
            video,
            songaudio,
            songvideo,
            format_id,
        )
        t0 = time.monotonic()
        if videoid:
            link = self.base + link

        @asyncify
        def audio_dl():
            dl_t0 = time.monotonic()
            _log("info", "audio_dl() starting for: %s", link[:80])
            ydl_optssx = _base_ydl_opts(
                format=_AUDIO_FMT,
                outtmpl="downloads/%(id)s.%(ext)s",
            )
            # web_embedded is the only client that still serves audio-only
            # formats here; tv/mweb/web only return progressive itag 18.
            ydl_optssx["extractor_args"]["youtube"]["player_client"] = ["web_embedded"] + list(_YT_CLIENTS)

            info = extract_info_with_fallback(link, ydl_optssx)
            xyz = os.path.join("downloads", f"{info['id']}.{info['ext']}")
            if os.path.exists(xyz):
                fsize = os.path.getsize(xyz)
                if fsize < 10240:
                    _log("warning", "audio_dl() stale/incomplete file (%d bytes), re-downloading: %s", fsize, xyz)
                    try:
                        os.remove(xyz)
                    except Exception:
                        pass
                else:
                    _log("info", "audio_dl() file exists (%d bytes): %s", fsize, xyz)
                    return xyz
            with _get_youtube_dl()(ydl_optssx) as x:
                x.download([link])
                final_size = os.path.getsize(xyz) if os.path.exists(xyz) else 0
                _log("info", "audio_dl() download done in %.1fs size=%d -> %s", time.monotonic() - dl_t0, final_size, xyz)
                return xyz

        @asyncify
        def video_dl():
            dl_t0 = time.monotonic()
            _log("info", "video_dl() starting for: %s", link[:80])
            ydl_optssx = _base_ydl_opts(
                format=_VIDEO_FMT,
                outtmpl="downloads/%(id)s.%(ext)s",
                merge_output_format="mp4",
            )

            info = extract_info_with_fallback(link, ydl_optssx)
            xyz = os.path.join("downloads", f"{info['id']}.{info['ext']}")
            if os.path.exists(xyz):
                fsize = os.path.getsize(xyz)
                if fsize < 10240:
                    _log("warning", "video_dl() stale/incomplete file (%d bytes), re-downloading: %s", fsize, xyz)
                    try:
                        os.remove(xyz)
                    except Exception:
                        pass
                else:
                    _log("info", "video_dl() file exists (%d bytes): %s", fsize, xyz)
                    return xyz
            with _get_youtube_dl()(ydl_optssx) as x:
                x.download([link])
            final_size = os.path.getsize(xyz) if os.path.exists(xyz) else 0
            _log("info", "video_dl() download done in %.1fs size=%d -> %s", time.monotonic() - dl_t0, final_size, xyz)
            return xyz

        @asyncify
        def song_video_dl():
            dl_t0 = time.monotonic()
            # No explicit pick: go for the largest file the source has, up to
            # 8K (the VC ladder is capped at 2160 — see _VIDEO_FMT_8K).
            formats = f"{format_id}+bestaudio/best/18/best" if format_id else _VIDEO_FMT_8K
            _log("info", "song_video_dl() starting format=%s for: %s", formats, link[:80])
            ydl_optssx = _base_ydl_opts(
                format=formats,
                outtmpl=os.path.join("downloads", f"%(id)s_{format_id}.%(ext)s"),
                merge_output_format="mp4",
            )

            info = extract_info_with_fallback(link, ydl_optssx)
            with _get_youtube_dl()(ydl_optssx) as x:
                filename = f"{info['id']}_{format_id}.mp4"
                file_path = os.path.join("downloads", filename)
                return file_path

        @asyncify
        def song_audio_dl():
            dl_t0 = time.monotonic()
            # "320" is the picker's highest-audio choice, not a yt-dlp format
            # id: pull the best native audio and hold the mp3 transcode at
            # CBR 320 kbps (YouTube's own streams top out near 130 kbps).
            native_format = format_id == "320"
            dl_format = _AUDIO_FMT if (native_format or not format_id) else format_id
            _log("info", "song_audio_dl() starting format=%s for: %s", dl_format, link[:80])
            ydl_optssx = _base_ydl_opts(
                format=dl_format,
                outtmpl=os.path.join("downloads", f"%(id)s_{format_id}.%(ext)s"),
                postprocessors=[
                    {
                        "key": "FFmpegExtractAudio",
                        # mp3, not opus: the returned filename below is .mp3,
                        # so an opus target would convert to .opus and the
                        # caller would hand Telegram a path that never
                        # existed. 320 kbps matches the media API's abr=320
                        # lane.
                        "preferredcodec": "mp3",
                        "preferredquality": "320",
                    }
                ],
                postprocessor_args={"ffmpeg": ["-threads", "4", "-b:a", "320k"]},
            )

            info = extract_info_with_fallback(link, ydl_optssx)
            with _get_youtube_dl()(ydl_optssx) as x:
                filename = f"{info['id']}_{format_id}.mp3"
                file_path = os.path.join("downloads", filename)
                return file_path

        try:
            # Primary fast-path: Aoi Hosting Music API download (MP3 / MP4)
            if (getattr(config, "MEDIA_API_KEY", "") or "").strip():
                api_mtype = "video" if (video or songvideo) else "audio"
                api_file = await self._api_download(link, media_type=api_mtype)
                if api_file:
                    _log("info", "download() COMPLETED via Media API in %.1fs -> %s", time.monotonic() - t0, api_file)
                    return api_file, True

            if songvideo:
                _log("info", "download() dispatching song_video_dl()")
                result = await asyncio.wait_for(
                    song_video_dl(), timeout=_YT_DLP_TIMEOUT * 3
                )
            elif songaudio:
                _log("info", "download() dispatching song_audio_dl()")
                result = await asyncio.wait_for(
                    song_audio_dl(), timeout=_YT_DLP_TIMEOUT * 3
                )
            elif video:
                _log("info", "download() dispatching video_dl()")
                direct = True
                result = await asyncio.wait_for(
                    video_dl(), timeout=_YT_DLP_TIMEOUT * 3
                )
            else:
                _log("info", "download() dispatching audio_dl()")
                direct = True
                result = await asyncio.wait_for(
                    audio_dl(), timeout=_YT_DLP_TIMEOUT * 3
                )

            elapsed = time.monotonic() - t0
            _log("info", "download() COMPLETED in %.1fs result=%s", elapsed, str(result)[:100])
            return result, True
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - t0
            _log("error", "download() TIMEOUT after %.1fs", elapsed)
            try:
                import asyncio as _aio
                _aio.get_running_loop().create_task(
                    notify_owner("Youtube.download", Exception(f"yt-dlp download timed out after {elapsed:.0f}s"), f"link={link[:80]} video={video} songaudio={songaudio} songvideo={songvideo}")
                )
            except Exception:
                pass
            raise Exception(f"download timed out after {elapsed:.0f}s")
        except Exception as e:
            elapsed = time.monotonic() - t0
            _log("error", "download() FAILED after %.1fs err=%s", elapsed, e)
            try:
                import asyncio as _aio
                _aio.get_running_loop().create_task(
                    notify_owner("Youtube.download", e, f"link={link[:80]} elapsed={elapsed:.1f}s video={video}")
                )
            except Exception:
                pass
            raise
