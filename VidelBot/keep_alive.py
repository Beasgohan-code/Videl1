"""
Keep-alive & responsive `/admin` operator console (Render, Koyeb, Railway, Heroku, VPS …).

  • Serves an async aiohttp web app on 0.0.0.0:PORT
      GET  /                         → status landing / plain-text keep-alive ("<BOT> is running ✨")
      GET  /ping                     → lightweight plain-text keep-alive probe
      GET  /health                   → JSON: status, uptime, clone bots, encoder queue, container resources
      GET  /admin                    → responsive owner/admin web console (open via /dashboard in Telegram)
      POST /admin/api/login          → exchange one-time link or Telegram Mini App initData for session
      GET  /admin/api/stats          → time-series analytics, totals, live tasks, health & diagnostics
      GET  /admin/api/diagnostics    → secret-free runtime, storage, binary, plugin & security diagnostics
      POST /admin/api/tasks/cancel   → cancel a running task by job key (authenticated admin only)
      POST /admin/api/tasks/clear    → purge waiting tasks from encoder queue (authenticated admin only)
      GET  /admin/chart/{users|jobs}.png → server-rendered analytics charts
  • Pings KEEP_ALIVE_URL (auto-detected on most hosts) every KEEP_ALIVE_INTERVAL
    seconds so sleeping platforms stay awake.
"""
import asyncio
import html
import logging
import os
import time

from aiohttp import ClientSession, ClientTimeout, web

import config

log = logging.getLogger("videl.keep_alive")
_started = time.time()
_runner = None


def _health() -> dict:
    data: dict = {"status": "ok", "bot": config.BOT_NAME, "uptime_s": int(time.time() - _started)}
    try:
        from filestore.worker_bot.engine import worker_engine
        data["clone_bots_running"] = worker_engine.active_count
    except Exception:
        data["clone_bots_running"] = 0
    try:
        from VideoEncoder import data as enc_queue
        data["encoder_queue"] = len(enc_queue)
    except Exception:
        data["encoder_queue"] = 0
    try:
        from core.sysinfo import system_snapshot
        snap = system_snapshot(".")
        data["resources"] = {
            "cpu_percent": snap["cpu_percent"],
            "cpu_cores": snap["cpu_cores"],
            "mem_percent": snap["mem_percent"],
            "rss_mb": round(snap["rss_bytes"] / 1048576, 1),
            "disk_free_gb": round(snap["disk_free_bytes"] / 1073741824, 2),
        }
    except Exception:
        pass
    try:
        import watchdog
        if watchdog.dog:
            data["watchdog"] = watchdog.dog.summary()
    except Exception:
        pass
    return data


LANDING_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{BOT}} · Status</title><style>
:root{color-scheme:dark;--bg:#0b0e17;--card:#141927;--line:#252d44;--fg:#f1f4fc;--mut:#949db8;--acc:#4f7cff;--ok:#34c759}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;
font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:radial-gradient(48rem 28rem at 50% -10%,rgba(79,124,255,.18),transparent 70%),var(--bg);color:var(--fg)}
.box{max-width:460px;width:100%;background:var(--card);border:1px solid var(--line);border-radius:20px;padding:32px 28px;text-align:center;box-shadow:0 20px 50px rgba(0,0,0,.35)}
.badge{display:inline-flex;align-items:center;gap:8px;padding:5px 12px;border-radius:99px;background:rgba(52,199,89,.12);color:#6ee792;font-size:13px;font-weight:600;margin-bottom:14px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--ok);box-shadow:0 0 10px var(--ok)}
h1{margin:0 0 8px;font-size:24px}p{margin:0 0 20px;color:var(--mut)}
.actions{display:flex;gap:10px;justify-content:center;flex-wrap:wrap}
a{text-decoration:none;padding:9px 16px;border-radius:10px;font-weight:600;font-size:14px;border:1px solid var(--line);color:var(--fg);background:#1b2234}
a.pri{background:var(--acc);border-color:var(--acc);color:#fff}
</style></head><body><div class="box">
<div class="badge"><span class="dot"></span>Online</div>
<h1>{{BOT}} is running ✨</h1>
<p>Multi-Bot SaaS Platform · WzGram MTProto + Bot API 10.3 + Richgram UI</p>
<div class="actions"><a class="pri" href="/app">🚀 SaaS Studio</a><a href="/admin">📊 Admin Console</a><a href="/health">🩺 Health JSON</a></div>
</div></body></html>"""


async def _ping(_):
    return web.Response(text=f"{config.BOT_NAME} is running ✨")


async def _root(request):
    accept = (request.headers.get("Accept") or "").lower()
    if "text/html" in accept:
        return web.Response(
            text=LANDING_HTML.replace("{{BOT}}", _html_escape(config.BOT_NAME)),
            content_type="text/html",
        )
    return web.Response(text=f"{config.BOT_NAME} is running ✨")


async def _health_handler(_):
    return web.json_response(_health())


# ─────────────────────────── /admin web dashboard ───────────────────────────
_fails: dict = {}          # ip → [timestamps] of wrong tokens
FAIL_WINDOW, FAIL_MAX = 600, 10
LINK_TTL = 600             # one-time links from /dashboard
INIT_MAX_AGE = 3600        # Mini App launch data older than this is refused
_links: dict = {}          # sha256(link key) → expiry
_sessions: dict = {}       # sha256(session) → (expiry, user id)


def enabled() -> bool:
    return bool(config.WEB_DASHBOARD or config.ADMIN_WEB_TOKEN)


def _h(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()


def _sweep(now: float):
    for box in (_links, _sessions):
        for k in [k for k, v in box.items() if (v[0] if isinstance(v, tuple) else v) < now]:
            box.pop(k, None)


def issue_link() -> str:
    """A one-time sign-in key for /admin#l.<key> (10 minutes, single use)."""
    import secrets
    now = time.time()
    _sweep(now)
    if len(_links) > 500:
        _links.clear()
    key = secrets.token_urlsafe(24)
    _links[_h(key)] = now + LINK_TTL
    return key


def _new_session(uid: int = 0) -> str:
    import secrets
    now = time.time()
    _sweep(now)
    if len(_sessions) > 2000:
        _sessions.clear()
    tok = "s." + secrets.token_urlsafe(32)
    _sessions[_h(tok)] = (now + config.WEB_SESSION_HOURS * 3600, int(uid or 0))
    return tok


def check_init_data(init_data: str, bot_token: str, max_age: int = INIT_MAX_AGE) -> dict | None:
    """Telegram Mini App launch data → the user dict when the signature is valid and fresh, else None."""
    import hashlib
    import hmac
    import json
    from urllib.parse import parse_qsl
    if not init_data or not bot_token or len(init_data) > 8192:
        return None
    try:
        fields = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None
    given = fields.pop("hash", "")
    if not given:
        return None
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    ok = False
    for drop in ((), ("signature",)):
        dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()) if k not in drop)
        if hmac.compare_digest(hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest(), given):
            ok = True
            break
    if not ok:
        return None
    try:
        if time.time() - int(fields.get("auth_date") or 0) > max_age:
            return None
        user = json.loads(fields.get("user") or "{}")
    except (TypeError, ValueError):
        return None
    return user if isinstance(user, dict) and user.get("id") else None


def _ip(request) -> str:
    return request.remote or "?"


def _limited(ip: str, now: float) -> bool:
    recent = [t for t in _fails.get(ip, []) if now - t < FAIL_WINDOW]
    _fails[ip] = recent
    return len(recent) >= FAIL_MAX


def _failed(ip: str, now: float):
    _fails.setdefault(ip, []).append(now)
    if len(_fails) > 5000:                    # bounded memory
        _fails.clear()


def _authorised(request) -> bool | None:
    """True ok · False wrong token · None rate-limited."""
    import hmac
    ip = _ip(request)
    now = time.time()
    if _limited(ip, now):
        return None
    given = request.headers.get("X-Admin-Token", "")
    if given:
        if config.ADMIN_WEB_TOKEN and hmac.compare_digest(given.encode(), config.ADMIN_WEB_TOKEN.encode()):
            _fails.pop(ip, None)
            return True
        sess = _sessions.get(_h(given))
        if sess and sess[0] > now:
            sess_uid = int(sess[1] or 0)
            if sess_uid == 0 or sess_uid in set(config.ADMINS):
                return True
            _sessions.pop(_h(given), None)
    _failed(ip, now)
    return False


def _guard(request):
    if not enabled():
        raise web.HTTPNotFound()
    ok = _authorised(request)
    if ok is None:
        raise web.HTTPTooManyRequests(text="too many attempts – wait 10 minutes")
    if not ok:
        raise web.HTTPUnauthorized(text="bad token")


def _days(request) -> int:
    try:
        return max(1, min(180, int(request.query.get("days", 30))))
    except ValueError:
        return 30


def _page_headers() -> dict:
    anc = config.WEB_FRAME_ANCESTORS or "'self'"
    return {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": f"frame-ancestors 'self' {anc}".strip()}


async def _admin_page(request):
    if not enabled():
        raise web.HTTPNotFound()
    return web.Response(text=DASHBOARD_HTML.replace("{{BOT}}", _html_escape(config.BOT_NAME)),
                        content_type="text/html", headers=_page_headers())


async def _admin_slash(_):
    raise web.HTTPFound("/admin")


async def _admin_login(request):
    """POST {"link": key} or {"init": Mini App initData} → {"session": token}."""
    if not enabled():
        raise web.HTTPNotFound()
    ip, now = _ip(request), time.time()
    if _limited(ip, now):
        raise web.HTTPTooManyRequests(text="too many attempts – wait 10 minutes")
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    link, init = str(body.get("link") or "")[:200], str(body.get("init") or "")
    if link:
        exp = _links.pop(_h(link), None)             # single use – gone even if it had expired
        if exp and exp > now:
            _fails.pop(ip, None)
            return web.json_response({"session": _new_session(), "via": "link"}, headers={"Cache-Control": "no-store"})
    elif init:
        user = check_init_data(init, config.BOT_TOKEN)
        if user and int(user["id"]) in set(config.ADMINS):
            _fails.pop(ip, None)
            return web.json_response({"session": _new_session(int(user["id"])), "via": "telegram",
                                      "name": str(user.get("first_name") or "")[:64]},
                                     headers={"Cache-Control": "no-store"})
        if user:
            _failed(ip, now)
            raise web.HTTPForbidden(text="admins only")
    _failed(ip, now)
    raise web.HTTPUnauthorized(text="link expired or already used – send /dashboard again")


def _html_escape(s) -> str:
    return html.escape(str(s))


def _live() -> list:
    """Encoder & transfer tasks right now: running tasks with live telemetry + waiting queue."""
    out = []
    try:
        from VideoEncoder import data as q
        from VideoEncoder.utils import jobs, scheduler

        snap = jobs.snapshot()
        run = scheduler.running()
        matched_keys: set = set()
        by_user: dict[int, list[dict]] = {}
        for j in snap.values():
            by_user.setdefault(int(j.get("user_id") or 0), []).append(j)

        for i, m in enumerate(list(q)[:50]):
            src = getattr(m, "reply_to_message", None) or m
            media = getattr(src, "video", None) or getattr(src, "document", None)
            name = getattr(media, "file_name", None) or ""
            uid = scheduler.uid_of(m) or 0
            is_running = any(m is r for r in run) or (i == 0 and bool(snap) and not run)
            j_info = None
            if is_running:
                cands = [j for j in by_user.get(int(uid), []) if j["key"] not in matched_keys]
                if not cands:
                    cands = [j for j in snap.values() if j["key"] not in matched_keys]
                if cands:
                    j_info = cands[0]
                    matched_keys.add(j_info["key"])
            out.append({
                "pos": i + 1,
                "state": ("paused" if (j_info and j_info.get("paused")) else ("running" if is_running else "waiting")),
                "paused": bool(j_info and j_info.get("paused")),
                "mode": (j_info.get("stage") if j_info and j_info.get("stage") else (scheduler.mode_of(m) or "encode")),
                "engine": (j_info.get("engine") if j_info else "") or "",
                "user": uid,
                "file": str((j_info.get("name") if j_info and j_info.get("name") else name) or "")[:64],
                "size": int((j_info.get("total") if j_info and j_info.get("total") else getattr(media, "file_size", 0)) or 0),
                "processed": int(j_info.get("processed", 0) if j_info else 0),
                "progress": float(j_info.get("progress", 0.0) if j_info else 0.0),
                "speed": float(j_info.get("speed", 0.0) if j_info else 0.0),
                "speed_label": str(j_info.get("speed_label", "") if j_info else ""),
                "eta": int(j_info.get("eta", 0) if j_info else 0),
                "key": j_info["key"] if j_info else None,
            })

        for k, j in snap.items():
            if k in matched_keys:
                continue
            out.append({
                "pos": len(out) + 1,
                "state": "paused" if j.get("paused") else "running",
                "paused": bool(j.get("paused")),
                "mode": j.get("stage") or "transfer",
                "engine": j.get("engine") or "",
                "user": j.get("user_id") or 0,
                "file": str(j.get("name") or f"Task #{k}")[:64],
                "size": int(j.get("total") or 0),
                "processed": int(j.get("processed") or 0),
                "progress": float(j.get("progress") or 0.0),
                "speed": float(j.get("speed") or 0.0),
                "speed_label": str(j.get("speed_label") or ""),
                "eta": int(j.get("eta") or 0),
                "key": k,
            })
    except Exception:
        pass
    return out


async def _admin_stats(request):
    _guard(request)
    from core import analytics, diagnostics
    days = _days(request)
    data = await analytics.series(days)
    diag = diagnostics.collect_diagnostics()
    return web.json_response(
        {
            "days": days,
            "series": data,
            "totals": await analytics.totals(),
            "health": _health(),
            "live": _live(),
            "diagnostics": diag,
            "labels": {k: v[0] for k, v in analytics.METRICS.items()},
        },
        headers={"Cache-Control": "no-store"},
    )


async def _admin_diagnostics(request):
    _guard(request)
    from core import diagnostics
    return web.json_response(diagnostics.collect_diagnostics(), headers={"Cache-Control": "no-store"})


async def _admin_cancel_task(request):
    _guard(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    raw_key = (body if isinstance(body, dict) else {}).get("key")
    if raw_key is None or str(raw_key).strip() == "":
        raise web.HTTPBadRequest(text="missing task key")
    s_key = str(raw_key).strip()
    job_key = int(s_key) if s_key.lstrip("-").isdigit() else s_key
    from VideoEncoder.utils import jobs
    cancelled = await jobs.cancel(job_key)
    return web.json_response({"ok": True, "cancelled": bool(cancelled), "key": job_key}, headers={"Cache-Control": "no-store"})


async def _admin_clear_queue(request):
    _guard(request)
    from VideoEncoder.plugins.queue import purge_waiting
    removed = await purge_waiting()
    return web.json_response({"ok": True, "removed": int(removed)}, headers={"Cache-Control": "no-store"})


async def _admin_task_action(request):
    _guard(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    payload = body if isinstance(body, dict) else {}
    action = str(payload.get("action") or "").strip().lower()
    if action not in ("cancel", "pause", "resume", "boost"):
        raise web.HTTPBadRequest(text="unsupported task action")

    from VideoEncoder.utils import jobs, scheduler

    if action == "boost":
        pos = int(payload.get("pos") or 0)
        if pos <= 0:
            raise web.HTTPBadRequest(text="invalid queue position")
        ok = scheduler.promote_to_front(pos)
        return web.json_response({"ok": True, "action": "boost", "applied": bool(ok), "pos": pos}, headers={"Cache-Control": "no-store"})

    raw_key = payload.get("key")
    if raw_key is None or str(raw_key).strip() == "":
        raise web.HTTPBadRequest(text="missing task key")
    s_key = str(raw_key).strip()
    job_key = int(s_key) if s_key.lstrip("-").isdigit() else s_key
    if action == "cancel":
        applied = await jobs.cancel(job_key)
    elif action == "pause":
        applied = jobs.pause(job_key)
    else:
        applied = jobs.resume(job_key)
    return web.json_response(
        {"ok": True, "action": action, "applied": bool(applied), "key": job_key},
        headers={"Cache-Control": "no-store"},
    )


def _workspace_roots() -> dict[str, str]:
    roots = {
        "downloads": os.path.realpath(getattr(config, "DOWNLOAD_DIR", "VideoEncoder/utils/extras/downloads")),
        "encodes": os.path.realpath(getattr(config, "ENCODE_DIR", "VideoEncoder/utils/extras/encodes")),
        "rename": os.path.realpath("downloads/rename"),
    }
    return roots


def _active_workspace_locks() -> set[str]:
    """Return directory/file basenames currently owned by queued or running tasks so cleanup cannot touch them."""
    locked: set[str] = set()
    try:
        from VideoEncoder import data as enc_q
        from VideoEncoder.utils import jobs, scheduler
        for m in list(enc_q):
            tk = scheduler.task_key(m)
            if tk:
                locked.add(tk)
        for j in jobs.active():
            if j.name:
                locked.add(os.path.basename(j.name))
    except Exception:
        pass
    try:
        from renamer import engine as rn
        for j in list(rn._jobs.values()):
            locked.add(f"{j.uid}_{j.id}_")
    except Exception:
        pass
    return locked


def _list_workspace_Sync() -> list[dict]:
    import os
    roots = _workspace_roots()
    locks = _active_workspace_locks()
    now = time.time()
    items: list[dict] = []
    for bucket, rpath in roots.items():
        if not os.path.isdir(rpath):
            continue
        for entry in sorted(os.listdir(rpath))[:100]:
            if entry.startswith("."):
                continue
            full = os.path.realpath(os.path.join(rpath, entry))
            if os.path.commonpath([rpath, full]) != rpath or full == rpath:
                continue
            try:
                is_dir = os.path.isdir(full)
                mtime = os.path.getmtime(full)
                if is_dir:
                    sz = 0
                    for wr, _, files in os.walk(full):
                        for fn in files:
                            try:
                                sz += os.path.getsize(os.path.join(wr, fn))
                            except OSError:
                                pass
                else:
                    sz = os.path.getsize(full)
            except OSError:
                continue
            is_locked = (
                entry in locks
                or entry.startswith("process")
                or entry == "_srccache"
                or any(entry.startswith(lk) for lk in locks if lk.endswith("_"))
            )
            items.append({
                "id": f"{bucket}:{entry}",
                "bucket": bucket,
                "name": entry,
                "kind": "folder" if is_dir else "file",
                "size": int(sz),
                "age_min": max(0, int((now - mtime) // 60)),
                "locked": bool(is_locked),
            })
    return items


async def _admin_workspace(request):
    _guard(request)
    items = await asyncio.to_thread(_list_workspace_Sync)
    return web.json_response({"items": items}, headers={"Cache-Control": "no-store"})


def _clean_workspace_sync(ids: list[str]) -> tuple[int, int, int]:
    import os
    import shutil
    roots = _workspace_roots()
    locks = _active_workspace_locks()
    removed = skipped = freed = 0
    for raw_id in ids[:100]:
        bucket, sep, entry = str(raw_id or "").partition(":")
        if not sep or bucket not in roots or not entry or "/" in entry or "\\" in entry or entry in (".", ".."):
            skipped += 1
            continue
        if (
            entry in locks
            or entry.startswith((".", "process"))
            or entry == "_srccache"
            or any(entry.startswith(lk) for lk in locks if lk.endswith("_"))
        ):
            skipped += 1
            continue
        rpath = roots[bucket]
        full = os.path.realpath(os.path.join(rpath, entry))
        if os.path.commonpath([rpath, full]) != rpath or full == rpath or not os.path.exists(full):
            skipped += 1
            continue
        try:
            if os.path.isdir(full) and not os.path.islink(full):
                for wr, _, files in os.walk(full):
                    for fn in files:
                        try:
                            freed += os.path.getsize(os.path.join(wr, fn))
                        except OSError:
                            pass
                shutil.rmtree(full, ignore_errors=True)
            else:
                freed += os.path.getsize(full)
                os.remove(full)
            removed += 1
        except OSError:
            skipped += 1
    return removed, freed, skipped


async def _admin_workspace_clean(request):
    _guard(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    ids = (body if isinstance(body, dict) else {}).get("ids") or []
    if not isinstance(ids, list) or not ids:
        raise web.HTTPBadRequest(text="no workspace items selected")
    removed, freed, skipped = await asyncio.to_thread(_clean_workspace_sync, [str(x) for x in ids])
    return web.json_response(
        {"ok": True, "removed": removed, "freed_bytes": freed, "skipped": skipped},
        headers={"Cache-Control": "no-store"},
    )


async def _admin_chart(request):
    _guard(request)
    from core import analytics
    users, work, _ = await analytics.charts(_days(request))
    png = users if request.match_info["name"] == "users" else work
    return web.Response(body=png, content_type="image/png", headers={"Cache-Control": "no-store"})


DASHBOARD_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>{{BOT}} · Admin Console</title><script src="https://telegram.org/js/telegram-web-app.js" async></script><style>
:root{color-scheme:dark;--bg:#0d1018;--card:#161b28;--card2:#1c2334;--line:#29324a;--fg:#eef2fb;--mut:#949eb8;
--acc:#4f7cff;--ok:#34c759;--warn:#f5a623;--bad:#ff5c6c}
*{box-sizing:border-box}body{margin:0;font:14.5px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
background:radial-gradient(60rem 26rem at 50% -12%,rgba(79,124,255,.14),transparent 70%),var(--bg);color:var(--fg)}
header{display:flex;gap:12px;align-items:center;justify-content:space-between;padding:16px 22px;border-bottom:1px solid var(--line);
background:rgba(13,16,24,.82);backdrop-filter:blur(10px);position:sticky;top:0;z-index:10;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
h1{font-size:19px;margin:0}h2{font-size:13.5px;margin:24px 0 10px;color:var(--mut);font-weight:700;letter-spacing:.5px;text-transform:uppercase;display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}
main{padding:18px 22px 42px;max-width:1240px;margin:auto}
.toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:11px;margin-bottom:10px}
.grid3{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px}
.k{color:var(--mut);font-size:12.5px}.v{font-size:22px;font-weight:700;margin-top:3px}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px}
img{width:100%;border-radius:14px;border:1px solid var(--line);background:var(--card)}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:8px 9px;border-bottom:1px solid var(--line);text-align:right;vertical-align:middle}
th:first-child,td:first-child{text-align:left}th{color:var(--mut);font-weight:600;white-space:nowrap}td.l{text-align:left}
tr:last-child td{border-bottom:none}
.pill{display:inline-flex;align-items:center;gap:5px;padding:2px 9px;border-radius:99px;font-size:11.5px;font-weight:600;background:var(--card2);border:1px solid var(--line)}
.pill.running,.pill.ok{background:rgba(52,199,89,.15);border-color:rgba(52,199,89,.4);color:#6ee792}
.pill.waiting,.pill.paused{background:rgba(245,166,35,.14);border-color:rgba(245,166,35,.35);color:#ffd180}
.pill.off{color:var(--mut)}
.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0 18px;border-bottom:1px solid var(--line);padding-bottom:10px}
.tabs button[aria-selected="true"]{background:var(--acc);border-color:var(--acc);color:#fff;font-weight:600}
.modal-bg{position:fixed;inset:0;background:rgba(6,8,14,.74);display:flex;align-items:center;justify-content:center;z-index:40;padding:16px}
.modal-bg[hidden]{display:none}
.modal{background:var(--card);border:1px solid var(--line);border-radius:16px;max-width:420px;width:100%;padding:20px}
button.sm{padding:4px 9px;font-size:12px}
.bar{height:7px;background:#0b0e16;border-radius:99px;overflow:hidden;border:1px solid var(--line);margin-top:6px;min-width:95px}
.bar>i{display:block;height:100%;background:linear-gradient(90deg,var(--acc),#35c9ff);border-radius:99px;transition:width .35s ease}
button,select,input{background:var(--card2);color:var(--fg);border:1px solid var(--line);border-radius:9px;padding:7px 11px;font:inherit;font-size:13px}
button{cursor:pointer;transition:border-color .15s,background .15s}button:hover{border-color:var(--acc)}
button:focus-visible,input:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
button.on{background:var(--acc);border-color:var(--acc);color:#fff;font-weight:600}
button.danger{background:rgba(255,92,108,.12);border-color:rgba(255,92,108,.4);color:#ff8a96;padding:4px 9px;font-size:12px}
button.danger:hover{background:rgba(255,92,108,.24)}
#login{max-width:400px;margin:11vh auto;text-align:center;padding:0 16px}
.login-card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:28px 22px}
.err{color:var(--bad)}.banner{background:rgba(245,166,35,.13);border:1px solid rgba(245,166,35,.4);color:#ffd48a;padding:10px 14px;border-radius:11px;margin-bottom:14px;font-size:13px}
.toast{position:fixed;bottom:18px;right:18px;background:#1f2840;border:1px solid var(--acc);color:#fff;padding:10px 15px;border-radius:11px;font-size:13px;z-index:30;box-shadow:0 10px 30px rgba(0,0,0,.4)}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--ok);margin-right:6px}
.kv{display:flex;justify-content:space-between;gap:10px;padding:6px 0;border-bottom:1px solid rgba(41,50,74,.6);font-size:13px}
.kv:last-child{border-bottom:none}
@media(max-width:640px){header,main{padding-left:14px;padding-right:14px}.grid{grid-template-columns:repeat(2,1fr)}}
</style></head><body>
<div id="login" hidden><div class="login-card"><h1>📊 {{BOT}} · Admin</h1>
<p class="k">Open from Telegram: send <b>/dashboard</b> to the bot and tap <b>🌐 Open dashboard</b>.</p>
<p class="k">Or sign in with your <code>ADMIN_WEB_TOKEN</code>:</p>
<form onsubmit="event.preventDefault();save(document.getElementById('t').value)">
<input id="t" type="password" aria-label="Admin Web Token" autocomplete="current-password" style="width:100%" placeholder="Enter ADMIN_WEB_TOKEN">
<p style="margin:12px 0 0"><button class="on" type="submit" style="width:100%">Sign in</button></p></form>
<p id="lerr" class="err" role="alert"></p></div></div>

<div id="app" hidden>
<header>
  <div class="brand"><h1>📊 {{BOT}} · Admin</h1><span class="pill ok" id="rt_badge">Online</span></div>
  <div class="toolbar"><span id="ranges"></span><button id="ref_btn" onclick="load(true)" title="Refresh now">↻ Refresh</button><button onclick="signout()">Sign out</button></div>
</header>
<main>
  <div id="net_err" class="banner" role="status" hidden></div>
  <div id="warn_box" class="banner" hidden></div>
  <div class="grid3" id="meters" style="margin-bottom:12px"></div>

  <nav class="tabs" role="tablist" aria-label="Dashboard sections">
    <button role="tab" id="tab_overview" aria-selected="true" onclick="switchTab('overview')">📊 Overview</button>
    <button role="tab" id="tab_tasks" aria-selected="false" onclick="switchTab('tasks')">🎬 Tasks &amp; Queue</button>
    <button role="tab" id="tab_files" aria-selected="false" onclick="switchTab('files')">🗂 Workspace Files</button>
    <button role="tab" id="tab_diag" aria-selected="false" onclick="switchTab('diag')">🩺 Storage &amp; Config</button>
  </nav>

  <section id="sec_overview" role="tabpanel" aria-labelledby="tab_overview">
    <div class="grid" id="cards"></div>
    <h2><span>📈 Activity Charts</span></h2>
    <div class="charts"><img id="c_users" alt="Users chart"><img id="c_jobs" alt="Jobs chart"></div>
    <h2><span>📅 Day-by-Day Breakdown</span></h2>
    <div class="card scroll"><table id="tbl"></table></div>
  </section>

  <section id="sec_tasks" role="tabpanel" aria-labelledby="tab_tasks" hidden>
    <h2><span>🎬 Live Tasks &amp; Queue</span><button id="clr_btn" class="danger" onclick="clearWaiting()" hidden>🧹 Purge Waiting</button></h2>
    <div class="card scroll"><table id="live"></table></div>
  </section>

  <section id="sec_files" role="tabpanel" aria-labelledby="tab_files" hidden>
    <h2>
      <span>🗂 Media Workspace Scratch Files</span>
      <span class="toolbar">
        <button class="sm" onclick="toggleAllFiles()">☑️ Toggle Unlocked</button>
        <button class="sm" onclick="loadWorkspace()">↻ Scan</button>
        <button class="danger" id="del_sel_btn" onclick="cleanSelectedFiles()">🗑 Delete Selected</button>
      </span>
    </h2>
    <div class="card scroll"><table id="ws_tbl"><tr><td class="l">Click Scan or open this tab to inspect workspace files.</td></tr></table></div>
  </section>

  <section id="sec_diag" role="tabpanel" aria-labelledby="tab_diag" hidden>
    <h2><span>🩺 Storage, Binaries &amp; Runtime Readiness</span></h2>
    <div class="grid3" id="diag"></div>
  </section>

  <p class="k" id="upd" aria-live="polite"></p>
</main></div>
<div id="modal_bg" class="modal-bg" hidden>
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modal_title">
    <h1 id="modal_title" style="font-size:16.5px;margin-bottom:8px">Confirm Action</h1>
    <p id="modal_body" class="k" style="margin:0 0 16px;font-size:13.5px"></p>
    <div class="toolbar" style="justify-content:flex-end">
      <button id="modal_cancel" onclick="closeModal(false)">Cancel</button>
      <button id="modal_ok" class="danger" onclick="closeModal(true)">Confirm</button>
    </div>
  </div>
</div>
<div id="toast" class="toast" hidden></div>

<script>
let token=sessionStorage.getItem('vt')||'',days=30,boot=null,busy=false;
const H=location.hash.slice(1);
if(H){history.replaceState(null,'',location.pathname);
 const p=new URLSearchParams(H);
 if(p.get('tgWebAppData'))boot={init:p.get('tgWebAppData')};
 else if(H.startsWith('l.'))boot={link:decodeURIComponent(H.slice(2))};
 else{token=decodeURIComponent(H);sessionStorage.setItem('vt',token)}}
function tg(){return window.Telegram&&Telegram.WebApp}
function toast(msg){const el=document.getElementById('toast');el.textContent=msg;el.hidden=false;clearTimeout(el._t);el._t=setTimeout(()=>el.hidden=true,3200)}
function save(t){token=(t||'').trim();if(!token)return;sessionStorage.setItem('vt',token);load(true)}
function signout(){sessionStorage.removeItem('vt');token='';document.getElementById('app').hidden=true;document.getElementById('login').hidden=false;document.getElementById('lerr').textContent='Signed out.'}
function fail(msg){sessionStorage.removeItem('vt');token='';document.getElementById('app').hidden=true;document.getElementById('login').hidden=false;document.getElementById('lerr').textContent=msg}
async function signin(body){
 const r=await fetch('admin/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),cache:'no-store'});
 if(!r.ok){fail(r.status==429?'Too many attempts – wait 10 minutes.':r.status==403?'This console is restricted to bot admins.':'This sign-in link expired or was already used – send /dashboard again.');return false}
 token=(await r.json()).session;sessionStorage.setItem('vt',token);return true}
async function api(u,opts={}){
 const headers=Object.assign({'X-Admin-Token':token},opts.headers||{});
 const r=await fetch(u,Object.assign({},opts,{headers,cache:'no-store'}));
 if(!r.ok){const err=new Error(r.status+' '+await r.text());err.status=r.status;throw err}
 return r}
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function sum(a){return (a||[]).reduce((x,y)=>x+y,0)}
function bytes(n){n=Number(n||0);if(!n)return '—';const u=['B','KB','MB','GB','TB'];let i=0;while(n>=1024&&i<u.length-1){n/=1024;i++}return n.toFixed(i>1?1:0)+' '+u[i]}
function secs(s){s=Math.max(0,Math.round(Number(s||0)));if(!s)return '—';const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),r=s%60;return (h?h+'h ':'')+(m?m+'m ':'')+(h?'':r+'s')}
async function img(id,name){try{const b=await (await api('admin/chart/'+name+'.png?days='+days)).blob();const el=document.getElementById(id);if(el.src)URL.revokeObjectURL(el.src);el.src=URL.createObjectURL(b)}catch(_){}}

async function cancelTask(key,file){
 if(!(await askConfirm('Stop Task','Cancel active task "'+(file||key)+'"?')))return;
 try{await api('admin/api/tasks/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key})});
  toast('Task cancellation requested.');load(true)}catch(e){toast('Cancel failed: '+e.message)}}

async function taskAction(action,key,pos){
 try{const body=action==='boost'?{action,pos:Number(pos)}:{action,key};
  await api('admin/api/tasks/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  toast('Task '+action+' applied.');load(true)}catch(e){toast('Action failed: '+e.message)}}

async function clearWaiting(){
 if(!(await askConfirm('Purge Waiting Queue','Remove all waiting tasks from the encoder queue? Running tasks will continue.')))return;
 try{const d=await (await api('admin/api/tasks/clear',{method:'POST'})).json();
  toast('Purged '+d.removed+' waiting task(s).');load(true)}catch(e){toast('Purge failed: '+e.message)}}

let _modalResolve=null,curTab='overview';
function askConfirm(title,msg){
 document.getElementById('modal_title').textContent=title;
 document.getElementById('modal_body').textContent=msg;
 document.getElementById('modal_bg').hidden=false;
 document.getElementById('modal_ok').focus();
 return new Promise(res=>{_modalResolve=res})}
function closeModal(ok){
 document.getElementById('modal_bg').hidden=true;
 if(_modalResolve){const fn=_modalResolve;_modalResolve=null;fn(Boolean(ok))}}
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!document.getElementById('modal_bg').hidden)closeModal(false)});

function switchTab(name){
 curTab=name;
 ['overview','tasks','files','diag'].forEach(t=>{
  document.getElementById('sec_'+t).hidden=(t!==name);
  document.getElementById('tab_'+t).setAttribute('aria-selected',String(t===name))});
 if(name==='files')loadWorkspace()}

async function loadWorkspace(){
 const tb=document.getElementById('ws_tbl');
 try{
  const d=await (await api('admin/api/workspace')).json();
  const items=d.items||[];
  tb.innerHTML=items.length?
   '<tr><th class="l">Select</th><th class="l">Bucket</th><th class="l">Name</th><th>Type</th><th>Size</th><th>Age</th><th>State</th></tr>'+
   items.map(x=>'<tr><td class="l"><input type="checkbox" class="ws_cb" value="'+esc(x.id)+'" '+(x.locked?'disabled':'')+'></td>'+
    '<td class="l"><code>'+esc(x.bucket)+'</code></td><td class="l">'+esc(x.name)+'</td><td>'+esc(x.kind)+'</td>'+
    '<td>'+bytes(x.size)+'</td><td>'+x.age_min+'m</td>'+
    '<td><span class="pill '+(x.locked?'waiting':'ok')+'">'+(x.locked?'In Use':'Orphan/Idle')+'</span></td></tr>').join('')
   :'<tr><td class="l"><span class="dot"></span>Workspace directories are completely clean.</td></tr>';
 }catch(e){tb.innerHTML='<tr><td class="l err">Failed to load workspace: '+esc(e.message)+'</td></tr>'}}

function toggleAllFiles(){
 const boxes=[...document.querySelectorAll('.ws_cb:not(:disabled)')];
 const anyUnchecked=boxes.some(b=>!b.checked);
 boxes.forEach(b=>b.checked=anyUnchecked)}

async function cleanSelectedFiles(){
 const ids=[...document.querySelectorAll('.ws_cb:checked')].map(b=>b.value);
 if(!ids.length){toast('Select at least one unlocked workspace item first.');return}
 if(!(await askConfirm('Delete Workspace Files','Delete '+ids.length+' selected scratch item(s)? Active tasks are protected.')))return;
 try{const d=await (await api('admin/api/workspace/clean',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids})})).json();
  toast('Deleted '+d.removed+' item(s), freed '+bytes(d.freed_bytes)+'.');loadWorkspace()}catch(e){toast('Cleanup failed: '+e.message)}}

function renderDiag(dg){
 if(!dg)return;
 const rt=dg.runtime||{},rs=dg.resources||{},pl=dg.plugins||{},sec=dg.security||{};
 document.getElementById('rt_badge').textContent=(rt.engine||'WzGram')+' '+(rt.version||'')+(rt.layer?' · L'+rt.layer:'');
 document.getElementById('meters').innerHTML=[
  ['⚙️ Container CPU',rs.cpu_percent+'% · '+(rs.cpu_cores||1)+' vCPU',rs.cpu_percent||0],
  ['🧠 Memory'+(rs.container_mem?' (Quota)':''),rs.mem_percent+'% · '+bytes(rs.mem_used_bytes)+' / '+bytes(rs.mem_total_bytes)+' (RSS '+bytes(rs.rss_bytes)+')',rs.mem_percent||0],
  ['💽 Storage Disk',(100-(rs.disk_percent||0)).toFixed(1)+'% free · '+bytes(rs.disk_free_bytes)+' available',rs.disk_percent||0]
 ].map(m=>'<div class="card"><div class="k">'+esc(m[0])+'</div><div style="font-weight:600;margin-top:4px;font-size:13.5px">'+esc(m[1])+'</div><div class="bar"><i style="width:'+Math.min(100,Math.max(2,Number(m[2])))+'%"></i></div></div>').join('');

 const wb=document.getElementById('warn_box');
 if(dg.warnings&&dg.warnings.length){wb.hidden=false;wb.innerHTML='<b>⚠️ Configuration notes:</b> '+dg.warnings.map(esc).join(' · ')}else wb.hidden=true;

 const stRows=(dg.storage||[]).map(s=>'<div class="kv"><span>'+esc(s.label)+'</span><span class="pill '+(s.available?'ok':'off')+'">'+(s.available?'Ready':'Optional')+'</span></div>').join('');
 const binRows=(dg.binaries||[]).map(b=>'<div class="kv"><span><code>'+esc(b.cmd)+'</code> · '+esc(b.label)+'</span><span class="pill '+(b.available?'ok':(b.required?'waiting':'off'))+'">'+(b.available?'Installed':(b.required?'Missing':'Optional'))+'</span></div>').join('');
 const secRows=[
  ['Fernet secret encryption',sec.encryption_active],
  ['Single-instance MongoDB lease',sec.single_instance],
  ['Daily automated backups',sec.backup_enabled],
  ['Bot API 10.3 bridge (aiogram)',rt.botapi_enabled],
  ['Modular plugins ('+(pl.healthy||0)+'/'+(pl.enabled||0)+' healthy)',(pl.errors&&Object.keys(pl.errors).length===0)]
 ].map(r=>'<div class="kv"><span>'+esc(r[0])+'</span><span class="pill '+(r[1]?'ok':'off')+'">'+(r[1]?'Active':'Off')+'</span></div>').join('');
 document.getElementById('diag').innerHTML=
  '<div class="card"><div class="k" style="margin-bottom:6px;font-weight:600">☁️ STORAGE DESTINATIONS</div>'+stRows+'</div>'+
  '<div class="card"><div class="k" style="margin-bottom:6px;font-weight:600">🧰 MEDIA &amp; ARCHIVE TOOLS</div>'+binRows+'</div>'+
  '<div class="card"><div class="k" style="margin-bottom:6px;font-weight:600">🛡 RUNTIME &amp; PLUGINS</div>'+secRows+'</div>';
}

async function load(manual){
 if(!token){document.getElementById('login').hidden=false;return}
 if(busy)return;busy=true;
 const rb=document.getElementById('ref_btn');if(rb)rb.textContent='⏳…';
 try{
  const d=await (await api('admin/api/stats?days='+days)).json();
  document.getElementById('net_err').hidden=true;
  document.getElementById('login').hidden=true;document.getElementById('app').hidden=false;
  renderDiag(d.diagnostics);
  const t=d.totals,s=d.series,L=d.labels;
  const cards=[['Total users',t.users],['Active ('+days+'d)',sum(s.active)],['New ('+days+'d)',sum(s.new)],['Encodes',sum(s.encode)],
   ['Renames',sum(s.rename)],['Saves',sum(s.save)],['Leeches',sum(s.leech)],['Stars',sum(s.stars)+' ⭐'],
   ['Clone bots',t.clones_running+' / '+t.clones],['Encoder queue',t.queue],['Uptime',Math.round(d.health.uptime_s/3600)+' h']];
  document.getElementById('cards').innerHTML=cards.map(c=>'<div class="card"><div class="k">'+esc(c[0])+'</div><div class="v">'+esc(c[1].toLocaleString())+'</div></div>').join('');

  const lv=d.live||[];
  const waitingCount=lv.filter(x=>x.state==='waiting').length;
  document.getElementById('clr_btn').hidden=waitingCount===0;
  document.getElementById('live').innerHTML=lv.length?
   '<tr><th>#</th><th class="l">State</th><th class="l">Stage / Engine</th><th class="l">File &amp; Progress</th><th>Speed</th><th>ETA</th><th>Size</th><th>User</th><th>Action</th></tr>'+
   lv.map(x=>{
    const pct=Math.min(100,Math.max(0,Number(x.progress||0)));
    const spd=x.speed_label||(x.speed?bytes(x.speed)+'/s':'—');
    const eng=x.engine?' <span class="pill off">'+esc(x.engine)+'</span>':'';
    const pbar=(x.state==='running'||x.state==='paused')?'<div class="bar"><i style="width:'+Math.max(3,pct)+'%"></i></div><span class="k">'+pct.toFixed(1)+'%</span>':'';
    let act='—';
    if(x.key!=null){
     const prBtn=x.paused?'<button class="sm" data-k="'+esc(x.key)+'" onclick="taskAction(\'resume\',this.dataset.k)">▶ Resume</button>'
                         :'<button class="sm" data-k="'+esc(x.key)+'" onclick="taskAction(\'pause\',this.dataset.k)">⏸ Pause</button>';
     act='<span class="toolbar" style="justify-content:flex-end">'+prBtn+'<button class="danger" data-k="'+esc(x.key)+'" data-f="'+esc(x.file)+'" onclick="cancelTask(this.dataset.k,this.dataset.f)">✖ Stop</button></span>';
    }else if(x.state==='waiting'&&x.pos>1){
     act='<button class="sm" onclick="taskAction(\'boost\',null,'+Number(x.pos)+')">⚡ Boost</button>';
    }
    return '<tr><td>'+x.pos+'</td><td class="l"><span class="pill '+esc(x.state)+'">'+esc(x.state)+'</span></td>'+
     '<td class="l">'+esc(x.mode)+eng+'</td><td class="l"><div>'+esc(x.file||'Media')+'</div>'+pbar+'</td>'+
     '<td>'+esc(spd)+'</td><td>'+secs(x.eta)+'</td><td>'+bytes(x.size)+'</td><td>'+esc(x.user)+'</td><td>'+act+'</td></tr>'}).join('')
   :'<tr><td class="l"><span class="dot"></span>Idle – no active encodes or transfers right now.</td></tr>';

  const keys=['active','new','encode','rename','save','leech','tool','clone','stars'];
  let h='<tr><th>Day</th>'+keys.map(k=>'<th>'+esc(L[k])+'</th>').join('')+'</tr>';
  for(let i=s.days.length-1;i>=0;i--)h+='<tr><td>'+esc(s.days[i])+'</td>'+keys.map(k=>'<td>'+esc(s[k][i])+'</td>').join('')+'</tr>';
  document.getElementById('tbl').innerHTML=h;
  document.getElementById('ranges').innerHTML=[7,30,90].map(n=>'<button class="'+(n==days?'on':'')+'" onclick="days='+n+';load(true)">'+n+'d</button>').join(' ');
  document.getElementById('upd').textContent='Last updated '+new Date().toLocaleTimeString()+' · auto-refreshes every 15s';
  img('c_users','users');img('c_jobs','jobs');
  if(manual)toast('Dashboard refreshed.');
 }catch(e){
  if(e.status===401||e.status===403)fail(e.status===403?'This console is restricted to bot admins.':'Session expired – open it again from /dashboard.');
  else if(e.status===429)fail('Too many attempts – wait 10 minutes.');
  else{const nb=document.getElementById('net_err');nb.hidden=false;nb.textContent='⚠️ Temporary connection issue ('+e.message+'). Retrying automatically…';if(document.getElementById('app').hidden)fail('Connection failed – check server status.')}
 }finally{busy=false;if(rb)rb.textContent='↻ Refresh'}}

(async()=>{
 await new Promise(r=>setTimeout(r,150));
 const w=tg();if(w){try{w.ready();w.expand()}catch(_){}if(!boot&&!token&&w.initData)boot={init:w.initData}}
 if(boot&&!(await signin(boot)))return;
 load()})();
setInterval(()=>{if(token&&!document.hidden)load()},15000);
</script></body></html>"""


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", _root)
    app.router.add_get("/health", _health_handler)
    app.router.add_get("/ping", _ping)
    app.router.add_get("/admin", _admin_page)
    app.router.add_get("/admin/", _admin_slash)
    app.router.add_post("/admin/api/login", _admin_login)
    app.router.add_get("/admin/api/stats", _admin_stats)
    app.router.add_get("/admin/api/diagnostics", _admin_diagnostics)
    app.router.add_post("/admin/api/tasks/cancel", _admin_cancel_task)
    app.router.add_post("/admin/api/tasks/action", _admin_task_action)
    app.router.add_post("/admin/api/tasks/clear", _admin_clear_queue)
    app.router.add_get("/admin/api/workspace", _admin_workspace)
    app.router.add_post("/admin/api/workspace/clean", _admin_workspace_clean)
    app.router.add_get("/admin/chart/{name:users|jobs}.png", _admin_chart)
    from webapp import mount_webapp
    mount_webapp(app)
    return app


async def start_server():
    """Start the health web server (idempotent)."""
    global _runner
    if _runner:
        return
    app = make_app()
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
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
    from core.bg import track
    track(loop.create_task(start_server()), name="web-server")
    track(loop.create_task(self_ping_loop()), name="keepalive-ping")


async def stop_server():
    global _runner
    if _runner:
        await _runner.cleanup()
        _runner = None
