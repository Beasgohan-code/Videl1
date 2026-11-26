#
#  AoiMusic - Now Playing HTML + Pillow Thumbnail Generator.
#
#  Uses assets/now_playing_template.html + assets/now_playing_base.png as the visual base:
#    - Right neon circle  -> Bot's own profile photo (auto-fetched & cached once)
#    - Left rounded box   -> Song thumbnail
#    - Left info card     -> Song Title, Artist/Source, Duration, Requested By
#
#  4-Tier Rendering Pipeline:
#    1. Playwright persistent Chromium browser singleton (fastest HTML render)
#    2. Headless Chromium CLI (--user-data-dir, HOME=/tmp, exact 1280x720)
#    3. Selenium headless Chrome fallback (worker thread)
#    4. Pixel-identical Pillow compositor fallback in asyncio.to_thread() for
#       512MB Heroku dynos / slim Docker images without a browser installed.
#

import asyncio
import base64
import hashlib
import html as _html
import logging
import os
import re
import shutil
import tempfile
import unicodedata
from pathlib import Path
from typing import Optional

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageOps
from py_yt import VideosSearch

from AoiMusic.utils.rich_msg import url_ok

LOGGER = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
_ASSETS = _ROOT / "assets"
_BASE_IMG = _ASSETS / "now_playing_base.png"
_HTML_TPL = _ASSETS / "now_playing_template.html"
_FONT_BOLD = _ASSETS / "font.ttf"
_FONT_REG = _ASSETS / "font2.ttf"
_CACHE_DIR = _ROOT / "cache"
_BOT_AVATAR_CACHE = _CACHE_DIR / "bot_avatar.jpg"

_CANVAS_W = 1280
_CANVAS_H = 720
_MAX_CACHE_FILES = 30
_bot_avatar_checked = False

# Persistent Playwright browser singleton for fast, low-memory HTML renders
_PW_LOCK = asyncio.Lock()
_PW_INSTANCE = None
_PW_BROWSER = None


def normalize_fancy_telegram_name(text: str, max_len: int = 24) -> str:
    """Normalize stylized Unicode Telegram names/titles so fonts never show tofu boxes."""
    if not text:
        return "Unknown"
    raw = re.sub(r"<[^>]+>", "", str(text))
    norm = unicodedata.normalize("NFKD", raw)
    # Keep printable ASCII + common Latin/Indic characters, drop broken combining/private marks
    cleaned = "".join(
        ch for ch in norm
        if (32 <= ord(ch) <= 126) or (0x0900 <= ord(ch) <= 0x097F) or (0x00A0 <= ord(ch) <= 0x024F)
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        cleaned = "Unknown"
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1].rstrip() + "…"
    return cleaned


async def _pick(videoid):
    """maxresdefault when it exists, else the search thumbnail, else hqdefault."""
    direct = f"https://img.youtube.com/vi/{videoid}/maxresdefault.jpg"
    if await url_ok(direct):
        return direct
    fallback = f"https://img.youtube.com/vi/{videoid}/hqdefault.jpg"
    try:
        query = f"https://www.youtube.com/watch?v={videoid}"
        thumbnail = fallback
        results = VideosSearch(query, limit=1)
        for result in (await results.next())["result"]:
            thumbnail = result["thumbnails"][0]["url"].split("?")[0]
        return thumbnail
    except Exception:
        return fallback


async def gen_thumb(videoid, thumb=None):
    if thumb:
        return thumb
    return await _pick(videoid)


async def gen_qthumb(vidid, thumb=None):
    if thumb:
        return thumb
    return await _pick(vidid)


async def _ensure_bot_avatar() -> Optional[Path]:
    """Download and cache the bot's own Telegram profile photo once."""
    global _bot_avatar_checked
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if _BOT_AVATAR_CACHE.exists() and _BOT_AVATAR_CACHE.stat().st_size > 512:
        return _BOT_AVATAR_CACHE
    if _bot_avatar_checked:
        return None
    _bot_avatar_checked = True
    try:
        from AoiMusic import app

        me = await app.get_me()
        photo = getattr(me, "photo", None)
        file_id = getattr(photo, "big_file_id", None) or getattr(photo, "small_file_id", None)
        if file_id:
            dl = await app.download_media(file_id, file_name=str(_BOT_AVATAR_CACHE))
            if dl and os.path.exists(dl):
                return Path(dl)
    except Exception as exc:
        LOGGER.debug("Could not fetch bot profile photo for thumbnail: %s", exc)
    return None


async def _download_song_thumb(url: Optional[str], videoid: str) -> Optional[Path]:
    """Download the YouTube/song thumbnail into cache/."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    candidates = []
    if url and str(url).startswith(("http://", "https://")):
        candidates.append(str(url))
    if videoid and str(videoid) not in ("telegram", "soundcloud", "None") and "index_" not in str(videoid):
        candidates.append(f"https://i.ytimg.com/vi/{videoid}/hqdefault.jpg")

    dest = _CACHE_DIR / f"st_{hashlib.md5((str(videoid) + str(url)).encode()).hexdigest()[:10]}.jpg"
    if dest.exists() and dest.stat().st_size > 1024:
        return dest

    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
        for u in candidates:
            try:
                resp = await client.get(u)
                if resp.status_code == 200 and len(resp.content) > 1024:
                    dest.write_bytes(resp.content)
                    return dest
            except Exception:
                continue
    return None


def _file_to_data_uri(path: Optional[Path], mime: str = "image/png") -> str:
    if not path or not path.exists():
        return ""
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def render_now_playing_html(
    title: str,
    artist: str,
    duration: str,
    requester: str,
    song_thumb_path: Optional[Path],
    bot_avatar_path: Optional[Path],
    elapsed: str = "00:00",
) -> str:
    """Populate assets/now_playing_template.html with dynamic song & bot data."""
    tpl = _HTML_TPL.read_text(encoding="utf-8")
    song_uri = _file_to_data_uri(song_thumb_path, "image/jpeg") or _file_to_data_uri(_BASE_IMG, "image/png")
    bot_uri = _file_to_data_uri(bot_avatar_path, "image/jpeg") or song_uri

    repls = {
        "{{BASE_IMG_URI}}": _file_to_data_uri(_BASE_IMG, "image/png"),
        "{{FONT_BOLD_URI}}": _file_to_data_uri(_FONT_BOLD, "font/ttf"),
        "{{FONT_REG_URI}}": _file_to_data_uri(_FONT_REG, "font/ttf"),
        "{{SONG_THUMB_URI}}": song_uri,
        "{{BOT_AVATAR_URI}}": bot_uri,
        "{{TITLE}}": _html.escape(title),
        "{{ARTIST}}": _html.escape(artist),
        "{{DURATION}}": _html.escape(duration),
        "{{REQUESTER}}": _html.escape(requester),
        "{{ELAPSED}}": _html.escape(elapsed),
    }
    for k, v in repls.items():
        tpl = tpl.replace(k, v)
    return tpl


# ---------------------------------------------------------------------------
#  HTML-to-PNG Render Chain: Playwright -> Chromium CLI -> Selenium
# ---------------------------------------------------------------------------

async def _render_with_playwright(html_content: str, output_path: str) -> bool:
    """Tier 1: Render HTML to PNG using a persistent Playwright Chromium instance."""
    global _PW_INSTANCE, _PW_BROWSER
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return False

    page = None
    try:
        async with _PW_LOCK:
            if _PW_BROWSER is None or not _PW_BROWSER.is_connected():
                if _PW_INSTANCE is None:
                    _PW_INSTANCE = await async_playwright().start()
                _PW_BROWSER = await _PW_INSTANCE.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                    ],
                )
            browser = _PW_BROWSER

        page = await browser.new_page(viewport={"width": _CANVAS_W, "height": _CANVAS_H})
        await page.set_content(html_content, wait_until="load", timeout=15000)
        await page.screenshot(path=output_path, type="png")
        return os.path.isfile(output_path) and os.path.getsize(output_path) > 65536
    except Exception as exc:
        LOGGER.warning("Playwright thumbnail render failed: %s", exc)
        return False
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass


def _find_chromium_binary() -> Optional[str]:
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        path = shutil.which(name)
        if path:
            return path
    return None


async def _render_with_chromium_cli(html_content: str, output_path: str) -> bool:
    """Tier 2: Render HTML to PNG directly via async headless Chromium CLI."""
    binary = _find_chromium_binary()
    if not binary:
        return False

    tmp_html = None
    tmp_profile = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tf:
            tf.write(html_content)
            tmp_html = tf.name
        tmp_profile = tempfile.mkdtemp(prefix="thumb_chrome_")

        abs_out = os.path.abspath(output_path)
        cmd = [
            binary,
            "--headless=new",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            f"--user-data-dir={tmp_profile}",
            f"--window-size={_CANVAS_W},{_CANVAS_H}",
            f"--screenshot={abs_out}",
            f"file://{os.path.abspath(tmp_html)}",
        ]
        env = {**os.environ, "HOME": "/tmp", "TMPDIR": "/tmp"}
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            LOGGER.warning("Headless Chromium CLI timed out.")
            return False

        if os.path.isfile(abs_out) and os.path.getsize(abs_out) > 65536:
            with Image.open(abs_out) as im:
                if im.size != (_CANVAS_W, _CANVAS_H):
                    im.crop((0, 0, min(im.width, _CANVAS_W), min(im.height, _CANVAS_H))).save(abs_out, "PNG")
            return True
        if stderr:
            LOGGER.debug("Headless Chromium CLI error: %s", stderr.decode(errors="ignore")[:300])
        return False
    except Exception as exc:
        LOGGER.debug("Chromium CLI render failed: %s", exc)
        return False
    finally:
        if tmp_html and os.path.exists(tmp_html):
            try:
                os.remove(tmp_html)
            except OSError:
                pass
        if tmp_profile and os.path.exists(tmp_profile):
            shutil.rmtree(tmp_profile, ignore_errors=True)


def _render_with_selenium_sync(html_content: str, output_path: str) -> bool:
    """Tier 3: Synchronous Selenium fallback executed in a worker thread."""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except ImportError:
        return False

    tmp_html = None
    driver = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tf:
            tf.write(html_content)
            tmp_html = tf.name

        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--hide-scrollbars")
        opts.add_argument(f"--window-size={_CANVAS_W},{_CANVAS_H}")

        driver = webdriver.Chrome(options=opts)
        driver.execute_cdp_cmd(
            "Emulation.setDeviceMetricsOverride",
            {"width": _CANVAS_W, "height": _CANVAS_H, "deviceScaleFactor": 1, "mobile": False},
        )
        driver.get(f"file://{os.path.abspath(tmp_html)}")
        abs_out = os.path.abspath(output_path)
        driver.save_screenshot(abs_out)
        return os.path.isfile(abs_out) and os.path.getsize(abs_out) > 65536
    except Exception as exc:
        LOGGER.debug("Selenium thumbnail render failed: %s", exc)
        return False
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
        if tmp_html and os.path.exists(tmp_html):
            try:
                os.remove(tmp_html)
            except OSError:
                pass


async def render_html_thumbnail(html_content: str, output_path: str) -> bool:
    """Render HTML string to PNG using Playwright -> Chromium CLI -> Selenium."""
    if await _render_with_playwright(html_content, output_path):
        return True
    if await _render_with_chromium_cli(html_content, output_path):
        return True
    if await asyncio.to_thread(_render_with_selenium_sync, html_content, output_path):
        return True
    return False


async def close_thumbnail_browser() -> None:
    """Graceful shutdown hook for closing persistent Playwright Chromium browser."""
    global _PW_INSTANCE, _PW_BROWSER
    async with _PW_LOCK:
        if _PW_BROWSER is not None:
            try:
                await _PW_BROWSER.close()
            except Exception:
                pass
            _PW_BROWSER = None
        if _PW_INSTANCE is not None:
            try:
                await _PW_INSTANCE.stop()
            except Exception:
                pass
            _PW_INSTANCE = None


# ---------------------------------------------------------------------------
#  Tier 4: Pixel-Identical Pillow Fallback (Zero Browser RAM on Slim Dynos)
# ---------------------------------------------------------------------------

def _load_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    try:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    except Exception:
        pass
    return ImageFont.load_default()


def _render_pillow_card(
    title: str,
    artist: str,
    duration: str,
    requester: str,
    song_thumb_path: Optional[Path],
    bot_avatar_path: Optional[Path],
    out_png: Path,
    elapsed: str = "00:00",
) -> Path:
    """Fast, 512MB-RAM-safe Pillow compositor matching assets/now_playing_template.html pixel-for-pixel."""
    base = Image.open(_BASE_IMG).convert("RGBA")
    if base.size != (_CANVAS_W, _CANVAS_H):
        base = base.resize((_CANVAS_W, _CANVAS_H), Image.LANCZOS)

    draw = ImageDraw.Draw(base)

    # 1. Left rounded square box: Song Thumbnail (44, 230, 182x182, r=18)
    if song_thumb_path and song_thumb_path.exists():
        try:
            st = Image.open(song_thumb_path).convert("RGBA")
            st = ImageOps.fit(st, (182, 182), method=Image.LANCZOS)
            mask = Image.new("L", (182, 182), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, 181, 181], radius=18, fill=255)
            base.paste(st, (44, 230), mask)
            draw.rounded_rectangle([44, 230, 226, 412], radius=18, outline=(236, 210, 255, 235), width=2)
        except Exception:
            pass

    # 2. Right neon purple circle: Bot Profile Photo (824, 122, 350x350)
    avatar_src = bot_avatar_path if (bot_avatar_path and bot_avatar_path.exists()) else song_thumb_path
    if avatar_src and avatar_src.exists():
        try:
            av = Image.open(avatar_src).convert("RGBA")
            av = ImageOps.fit(av, (350, 350), method=Image.LANCZOS)
            cmask = Image.new("L", (350, 350), 0)
            ImageDraw.Draw(cmask).ellipse([0, 0, 349, 349], fill=255)
            base.paste(av, (824, 122), cmask)
            draw.ellipse([824, 122, 1174, 472], outline=(244, 224, 255, 220), width=3)
        except Exception:
            pass

    # 3. Clean dark glass plates covering placeholder text (preserving icons & divider line at y=335)
    draw.rounded_rectangle([318, 236, 710, 328], radius=8, fill=(9, 3, 31, 255))
    draw.rounded_rectangle([318, 342, 710, 414], radius=8, fill=(11, 4, 37, 255))

    f_title = _load_font(_FONT_BOLD, 33)
    f_meta = _load_font(_FONT_REG, 22)
    f_meta_b = _load_font(_FONT_BOLD, 22)
    f_time = _load_font(_FONT_BOLD, 23)

    draw.text((326, 242), title, font=f_title, fill=(255, 255, 255, 255))
    draw.text((326, 292), artist, font=f_meta, fill=(230, 217, 255, 255))
    draw.text((326, 348), f"Duration : {duration}", font=f_meta, fill=(243, 236, 255, 255))

    req_prefix = "Requested by : "
    draw.text((326, 383), req_prefix, font=f_meta, fill=(243, 236, 255, 255))
    try:
        pw = int(draw.textlength(req_prefix, font=f_meta))
    except Exception:
        pw = 158
    draw.text((326 + pw, 383), requester, font=f_meta_b, fill=(217, 70, 239, 255))

    # 4. Progress bar timestamps
    draw.rectangle([34, 468, 126, 502], fill=(6, 2, 26, 255))
    draw.rectangle([630, 468, 728, 502], fill=(6, 2, 26, 255))
    draw.text((34, 470), elapsed, font=f_time, fill=(255, 255, 255, 255))
    try:
        dw = int(draw.textlength(duration, font=f_time))
    except Exception:
        dw = 64
    draw.text((726 - dw, 470), duration, font=f_time, fill=(255, 255, 255, 255))

    out_png.parent.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(out_png, "PNG", optimize=True)
    return out_png


def _prune_thumb_cache() -> None:
    try:
        files = sorted(
            [p for p in _CACHE_DIR.glob("np_*.png")],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in files[_MAX_CACHE_FILES:]:
            try:
                old.unlink()
            except OSError:
                pass
    except Exception:
        pass


async def build_now_playing_card(
    videoid: str,
    title: str,
    duration: str,
    requester: str,
    artist: str = "Videl Music",
    song_thumb: Optional[str] = None,
    elapsed: str = "00:00",
    bot_avatar_override: Optional[Path] = None,
) -> Optional[str]:
    """Generate the neon purple Now Playing thumbnail PNG from HTML template (4-tier pipeline)."""
    if not _BASE_IMG.exists():
        return song_thumb

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    clean_title = normalize_fancy_telegram_name(title, max_len=21)
    clean_artist = normalize_fancy_telegram_name(artist or "Videl Music", max_len=28)
    clean_req = normalize_fancy_telegram_name(requester or "User", max_len=16)
    clean_dur = str(duration or "00:00").strip()[:10]
    clean_el = str(elapsed or "00:00").strip()[:10]

    song_path, bot_path = await asyncio.gather(
        _download_song_thumb(song_thumb, str(videoid or "")),
        asyncio.sleep(0, result=bot_avatar_override) if bot_avatar_override else _ensure_bot_avatar(),
    )

    sig = hashlib.md5(
        f"{videoid}|{clean_title}|{clean_artist}|{clean_dur}|{clean_req}|{clean_el}".encode()
    ).hexdigest()[:12]
    out_png = _CACHE_DIR / f"np_{sig}.png"

    if out_png.is_file() and out_png.stat().st_size > 65536:
        return str(out_png)

    try:
        # Tiers 1–3: Browser HTML/CSS render (opt-in via THUMB_USE_BROWSER=true to save ~150MB RAM)
        use_browser = os.getenv("THUMB_USE_BROWSER", "false").strip().lower() in ("1", "true", "yes")
        if use_browser and _HTML_TPL.exists():
            html_str = await asyncio.to_thread(
                render_now_playing_html,
                clean_title,
                clean_artist,
                clean_dur,
                clean_req,
                song_path,
                bot_path,
                clean_el,
            )
            if await render_html_thumbnail(html_str, str(out_png)):
                _prune_thumb_cache()
                return str(out_png)

        # Tier 4: Ultra-fast pixel-identical Pillow fallback (zero browser RAM on Heroku)
        await asyncio.to_thread(
            _render_pillow_card,
            clean_title,
            clean_artist,
            clean_dur,
            clean_req,
            song_path,
            bot_path,
            out_png,
            clean_el,
        )
        _prune_thumb_cache()
        return str(out_png)
    except Exception as exc:
        LOGGER.warning("build_now_playing_card failed: %s", exc)
        return song_thumb
