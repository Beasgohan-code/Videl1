"""
Keep-alive for free / sleeping hosts (Render, Koyeb, Railway, Heroku, Replit …).

  • Serves a tiny web app on 0.0.0.0:PORT
      GET /          → "Videl is running ✨"
      GET /health    → JSON: status, uptime, clone bots, encoder queue, last watchdog sweep
      GET /admin     → owner web dashboard (open it from Telegram with /dashboard)
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


# ─────────────────────────── /admin web dashboard ───────────────────────────
# Sign-in, three ways – none of them puts a secret where the server could log it:
#   🌐 Mini App  /dashboard in Telegram → the page opens inside Telegram, which signs the launch data with the
#               bot token (HMAC) → the page trades it for a session. Admins only (user id checked).
#   🔗 link      /dashboard → one-time link /admin#l.<key> (10 min, single use) → session.
#   🔑 token     /admin#<ADMIN_WEB_TOKEN> (optional, when that variable is set).
# The secret always travels in the URL *fragment* (never sent to the server, never in logs / referrers) and the
# page sends the session as the X-Admin-Token header. Wrong tries are rate-limited per client.
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
    """Telegram Mini App launch data → the user dict when the signature is valid and fresh, else None.

    hash = HMAC-SHA256(key=HMAC-SHA256("WebAppData", bot_token), data-check-string) over every field except
    `hash`, sorted, one per line (core.telegram.org/bots/webapps). Some clients' docs also leave out the newer
    `signature` field – both forms are accepted, each is still keyed by the bot token."""
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
    # The socket peer, NOT X-Forwarded-For (clients can forge it to dodge the limit). Behind a platform
    # proxy every visitor shares one bucket – fine for a single-admin page.
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
            return True
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
    raise web.HTTPFound("/admin")          # the page uses relative URLs (admin/api/…)


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
    import html
    return html.escape(str(s))


def _live() -> list:
    """Encoder tasks right now: running first, then the queue."""
    out = []
    try:
        from VideoEncoder import data as q
        from VideoEncoder.utils import scheduler
        run = scheduler.running()
        for i, m in enumerate(list(q)[:50]):
            src = getattr(m, "reply_to_message", None) or m
            media = getattr(src, "video", None) or getattr(src, "document", None)
            name = getattr(media, "file_name", None) or ""
            out.append({"pos": i + 1, "state": "running" if any(m is r for r in run) else "waiting",
                        "mode": scheduler.MODES.get(id(m)) or "?", "user": scheduler.uid_of(m),
                        "file": str(name)[:60], "size": int(getattr(media, "file_size", 0) or 0)})
    except Exception:
        pass
    return out


async def _admin_stats(request):
    _guard(request)
    from core import analytics
    days = _days(request)
    data = await analytics.series(days)
    return web.json_response({"days": days, "series": data, "totals": await analytics.totals(),
                              "health": _health(), "live": _live(),
                              "labels": {k: v[0] for k, v in analytics.METRICS.items()}},
                             headers={"Cache-Control": "no-store"})


async def _admin_chart(request):
    _guard(request)
    from core import analytics
    users, work, _ = await analytics.charts(_days(request))
    png = users if request.match_info["name"] == "users" else work
    return web.Response(body=png, content_type="image/png", headers={"Cache-Control": "no-store"})


DASHBOARD_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>{{BOT}} · Admin</title><script src="https://telegram.org/js/telegram-web-app.js" async></script><style>
:root{--bg:#12141c;--card:#1b1e29;--line:#2c3040;--fg:#eceef5;--mut:#969cb0;--acc:#5865f2;--ok:#3ba55d;--warn:#faa61a}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--fg)}
header{display:flex;gap:12px;align-items:center;justify-content:space-between;padding:18px 24px;border-bottom:1px solid var(--line);flex-wrap:wrap}
h1{font-size:20px;margin:0}h2{font-size:15px;margin:22px 0 10px;color:var(--mut);font-weight:600;letter-spacing:.3px}
main{padding:20px 24px;max-width:1200px;margin:auto}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px;margin-bottom:6px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.k{color:var(--mut);font-size:13px}.v{font-size:24px;font-weight:650;margin-top:4px}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:12px}
img{width:100%;border-radius:12px;border:1px solid var(--line);background:var(--card)}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:right}
th:first-child,td:first-child{text-align:left}th{color:var(--mut);font-weight:600}td.l{text-align:left}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;background:var(--line)}
.pill.running{background:var(--ok)}.pill.waiting{background:#4f545c}
button,select,input{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:7px 10px;font:inherit}
button{cursor:pointer}button.on{background:var(--acc);border-color:var(--acc)}#login{max-width:380px;margin:12vh auto;text-align:center;padding:0 16px}
.err{color:#ff6b6b}.scroll{overflow-x:auto}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--ok);margin-right:6px}</style></head><body>
<div id="login" hidden><h1>{{BOT}} · Admin</h1>
<p class="k">Open this page from Telegram: send <b>/dashboard</b> to the bot and tap <b>🌐 Open dashboard</b>.</p>
<p class="k">Or enter your ADMIN_WEB_TOKEN:</p>
<form onsubmit="event.preventDefault();save(document.getElementById('t').value)"><input id="t" type="password" autocomplete="current-password" style="width:100%">
<p><button type="submit">Open dashboard</button></p></form><p id="lerr" class="err"></p></div>
<div id="app" hidden><header><h1>📊 {{BOT}} · Admin</h1><div id="ranges"></div></header><main>
<div class="grid" id="cards"></div>
<h2>⚙️ ENCODER – LIVE</h2><div class="card scroll"><table id="live"></table></div>
<h2>📈 CHARTS</h2><div class="charts"><img id="c_users" alt="Users chart"><img id="c_jobs" alt="Jobs chart"></div>
<h2>📅 DAY BY DAY</h2><div class="card scroll"><table id="tbl"></table></div><p class="k" id="upd"></p></main></div>
<script>
let token=sessionStorage.getItem('vt')||'',days=30,boot=null;
const H=location.hash.slice(1);
if(H){history.replaceState(null,'',location.pathname);
 const p=new URLSearchParams(H);
 if(p.get('tgWebAppData'))boot={init:p.get('tgWebAppData')};
 else if(H.startsWith('l.'))boot={link:decodeURIComponent(H.slice(2))};
 else{token=decodeURIComponent(H);sessionStorage.setItem('vt',token)}}
function tg(){return window.Telegram&&Telegram.WebApp}
function save(t){token=t;sessionStorage.setItem('vt',t);load()}
function fail(msg){sessionStorage.removeItem('vt');token='';document.getElementById('app').hidden=true;document.getElementById('login').hidden=false;document.getElementById('lerr').textContent=msg}
async function signin(body){const r=await fetch('admin/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),cache:'no-store'});
 if(!r.ok){fail(r.status==429?'Too many attempts – wait 10 minutes.':r.status==403?'This dashboard is for the bot admins only.':'This link expired or was already used – send /dashboard again.');return false}
 token=(await r.json()).session;sessionStorage.setItem('vt',token);return true}
async function get(u){const r=await fetch(u,{headers:{'X-Admin-Token':token},cache:'no-store'});if(!r.ok)throw new Error(r.status+' '+await r.text());return r}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function sum(a){return a.reduce((x,y)=>x+y,0)}
function mb(n){return n?(n/1048576).toFixed(n>=1073741824?0:1)+' MB':''}
async function img(id,name){const b=await (await get('admin/chart/'+name+'.png?days='+days)).blob();const el=document.getElementById(id);if(el.src)URL.revokeObjectURL(el.src);el.src=URL.createObjectURL(b)}
async function load(){
 if(!token){document.getElementById('login').hidden=false;return}
 try{const d=await (await get('admin/api/stats?days='+days)).json();
  document.getElementById('login').hidden=true;document.getElementById('app').hidden=false;
  const t=d.totals,s=d.series,L=d.labels;
  const cards=[['Total users',t.users],['Active ('+days+'d)',sum(s.active)],['New ('+days+'d)',sum(s.new)],['Encodes',sum(s.encode)],
   ['Renames',sum(s.rename)],['Saves',sum(s.save)],['Leeches',sum(s.leech)],['Stars',sum(s.stars)+' ⭐'],
   ['Clone bots',t.clones_running+' / '+t.clones],['Encoder queue',t.queue],['Uptime',Math.round(d.health.uptime_s/3600)+' h']];
  document.getElementById('cards').innerHTML=cards.map(c=>'<div class="card"><div class="k">'+esc(c[0])+'</div><div class="v">'+esc(c[1].toLocaleString())+'</div></div>').join('');
  const lv=d.live||[];
  document.getElementById('live').innerHTML=lv.length?'<tr><th>#</th><th class="l">State</th><th class="l">Task</th><th class="l">File</th><th>Size</th><th>User</th></tr>'+
   lv.map(x=>'<tr><td>'+x.pos+'</td><td class="l"><span class="pill '+esc(x.state)+'">'+esc(x.state)+'</span></td><td class="l">'+esc(x.mode)+'</td><td class="l">'+esc(x.file)+'</td><td>'+mb(x.size)+'</td><td>'+esc(x.user)+'</td></tr>').join('')
   :'<tr><td class="l"><span class="dot"></span>Idle – nothing in the encoder queue.</td></tr>';
  const keys=['active','new','encode','rename','save','leech','tool','clone','stars'];
  let h='<tr><th>Day</th>'+keys.map(k=>'<th>'+esc(L[k])+'</th>').join('')+'</tr>';
  for(let i=s.days.length-1;i>=0;i--)h+='<tr><td>'+s.days[i]+'</td>'+keys.map(k=>'<td>'+s[k][i]+'</td>').join('')+'</tr>';
  document.getElementById('tbl').innerHTML=h;
  document.getElementById('ranges').innerHTML=[7,30,90].map(n=>'<button class="'+(n==days?'on':'')+'" onclick="days='+n+';load()">'+n+' days</button>').join(' ');
  document.getElementById('upd').textContent='Updated '+new Date().toLocaleString()+' · refreshes every 30 s';
  img('c_users','users');img('c_jobs','jobs');
 }catch(e){fail(String(e.message).startsWith('429')?'Too many attempts – wait 10 minutes.':'Signed out – open it again from /dashboard.')}}
(async()=>{
 await new Promise(r=>setTimeout(r,150));
 const w=tg();if(w){try{w.ready();w.expand()}catch(e){}if(!boot&&!token&&w.initData)boot={init:w.initData}}
 if(boot&&!(await signin(boot)))return;
 load()})();
setInterval(()=>{if(token&&!document.hidden)load()},30000);
</script></body></html>"""


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", _root)
    app.router.add_get("/health", _health_handler)
    app.router.add_get("/ping", _root)
    app.router.add_get("/admin", _admin_page)
    app.router.add_get("/admin/", _admin_slash)
    app.router.add_post("/admin/api/login", _admin_login)
    app.router.add_get("/admin/api/stats", _admin_stats)
    app.router.add_get("/admin/chart/{name:users|jobs}.png", _admin_chart)
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
    loop = loop or asyncio.get_event_loop()
    from core.bg import track
    track(loop.create_task(start_server(), name="web-server"))
    track(loop.create_task(self_ping_loop(), name="keepalive-ping"))


async def stop_server():
    global _runner
    if _runner:
        await _runner.cleanup()
        _runner = None
