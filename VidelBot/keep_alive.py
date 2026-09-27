"""
Keep-alive for free / sleeping hosts (Render, Koyeb, Railway, Heroku, Replit …).

  • Serves a tiny web app on 0.0.0.0:PORT
      GET /          → "Videl is running ✨"
      GET /health    → JSON: status, uptime, clone bots, encoder queue, last watchdog sweep
  • Pings KEEP_ALIVE_URL (auto-detected on most hosts) every KEEP_ALIVE_INTERVAL
    seconds so the platform never idles the process.
"""
import asyncio
import logging
import time

from aiohttp import ClientSession, ClientTimeout, web

import config

log = logging.getLogger("videl.keep_alive")
_started = time.time()
_runner = None


def _health() -> dict:
    data = {"status": "ok", "bot": config.BOT_NAME, "uptime_s": int(time.time() - _started)}
    try:
        from filestore.worker_bot.engine import worker_engine
        data["clone_bots_running"] = worker_engine.active_count
    except Exception:
        pass
    try:
        from VideoEncoder import data as enc_queue
        data["encoder_queue"] = len(enc_queue)
    except Exception:
        pass
    try:
        import watchdog
        if watchdog.dog:
            data["watchdog"] = watchdog.dog.summary()
    except Exception:
        pass
    return data


async def _root(_):
    return web.Response(text=f"{config.BOT_NAME} is running ✨")


async def _health_handler(_):
    return web.json_response(_health())


async def start_server():
    """Start the health web server (idempotent)."""
    global _runner
    if _runner:
        return
    app = web.Application()
    app.router.add_get("/", _root)
    app.router.add_get("/health", _health_handler)
    app.router.add_get("/ping", _root)
    _runner = web.AppRunner(app, access_log=None)
    await _runner.setup()
    await web.TCPSite(_runner, "0.0.0.0", config.PORT).start()
    log.info(f"🌐 keep-alive server listening on 0.0.0.0:{config.PORT}")


async def self_ping_loop():
    url = (config.KEEP_ALIVE_URL or "").rstrip("/")
    if not url:
        log.info("KEEP_ALIVE_URL not set/detected – self-ping disabled (not needed on a VPS).")
        return
    url += "/ping"
    log.info(f"🔁 self-ping every {config.KEEP_ALIVE_INTERVAL}s → {url}")
    fails = 0
    while True:
        await asyncio.sleep(config.KEEP_ALIVE_INTERVAL)
        try:
            async with ClientSession(timeout=ClientTimeout(total=20)) as s:
                async with s.get(url) as r:
                    fails = 0 if r.status < 500 else fails + 1
        except Exception as e:
            fails += 1
            if fails in (1, 5) or fails % 20 == 0:
                log.warning(f"self-ping failed ({fails}x): {e}")


def keep_alive(loop: asyncio.AbstractEventLoop = None):
    """Start server + pinger as background tasks on the running loop."""
    loop = loop or asyncio.get_event_loop()
    loop.create_task(start_server())
    loop.create_task(self_ping_loop())


async def stop_server():
    global _runner
    if _runner:
        await _runner.cleanup()
        _runner = None
