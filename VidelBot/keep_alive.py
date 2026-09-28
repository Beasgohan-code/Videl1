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


# ─────────────────────────── /admin web dashboard ───────────────────────────
# Off unless ADMIN_WEB_TOKEN is set. The token travels in the URL *fragment* (#token – never sent to the
# server, never in logs / referrers) and is sent by the page as the X-Admin-Token header.
_fails: dict = {}          # ip → [timestamps] of wrong tokens
FAIL_WINDOW, FAIL_MAX = 600, 10


def _ip(request) -> str:
    # The socket peer, NOT X-Forwarded-For (clients can forge it to dodge the limit). Behind a platform
    # proxy every visitor shares one bucket – fine for a single-admin page.
    return request.remote or "?"


def _authorised(request) -> bool | None:
    """True ok · False wrong token · None rate-limited."""
    import hmac
    ip = _ip(request)
    now = time.time()
    recent = [t for t in _fails.get(ip, []) if now - t < FAIL_WINDOW]
    if len(recent) >= FAIL_MAX:
        _fails[ip] = recent
        return None
    given = request.headers.get("X-Admin-Token", "")
    if given and hmac.compare_digest(given.encode(), config.ADMIN_WEB_TOKEN.encode()):
        _fails.pop(ip, None)
        return True
    recent.append(now)
    _fails[ip] = recent
    if len(_fails) > 5000:                    # bounded memory
        _fails.clear()
    return False


def _guard(request):
    if not config.ADMIN_WEB_TOKEN:
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


async def _admin_page(request):
    if not config.ADMIN_WEB_TOKEN:
        raise web.HTTPNotFound()
    return web.Response(text=DASHBOARD_HTML.replace("{{BOT}}", _html_escape(config.BOT_NAME)),
                        content_type="text/html", headers={"Cache-Control": "no-store", "X-Frame-Options": "SAMEORIGIN",
                                                          "Referrer-Policy": "no-referrer"})


async def _admin_slash(_):
    raise web.HTTPFound("/admin")          # the page uses relative URLs (admin/api/…)


def _html_escape(s) -> str:
    import html
    return html.escape(str(s))


async def _admin_stats(request):
    _guard(request)
    from core import analytics
    days = _days(request)
    data = await analytics.series(days)
    return web.json_response({"days": days, "series": data, "totals": await analytics.totals(),
                              "health": _health(), "labels": {k: v[0] for k, v in analytics.METRICS.items()}},
                             headers={"Cache-Control": "no-store"})


async def _admin_chart(request):
    _guard(request)
    from core import analytics
    users, work, _ = await analytics.charts(_days(request))
    png = users if request.match_info["name"] == "users" else work
    return web.Response(body=png, content_type="image/png", headers={"Cache-Control": "no-store"})


DASHBOARD_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>{{BOT}} · Admin</title><style>
:root{--bg:#12141c;--card:#1b1e29;--line:#2c3040;--fg:#eceef5;--mut:#969cb0;--acc:#5865f2}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--fg)}
header{display:flex;gap:12px;align-items:center;justify-content:space-between;padding:18px 24px;border-bottom:1px solid var(--line);flex-wrap:wrap}
h1{font-size:20px;margin:0}main{padding:20px 24px;max-width:1200px;margin:auto}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px;margin-bottom:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.k{color:var(--mut);font-size:13px}.v{font-size:24px;font-weight:650;margin-top:4px}
img{width:100%;border-radius:12px;border:1px solid var(--line);margin-bottom:14px;background:var(--card)}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:right}
th:first-child,td:first-child{text-align:left}th{color:var(--mut);font-weight:600}
button,select,input{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:7px 10px;font:inherit}
button{cursor:pointer}button.on{background:var(--acc);border-color:var(--acc)}#login{max-width:380px;margin:12vh auto;text-align:center}
.err{color:#ff6b6b}.scroll{overflow-x:auto}</style></head><body>
<div id="login" hidden><h1>{{BOT}} · Admin</h1><p class="k">Enter the ADMIN_WEB_TOKEN</p>
<form onsubmit="event.preventDefault();save(document.getElementById('t').value)"><input id="t" type="password" autocomplete="current-password" style="width:100%">
<p><button type="submit">Open dashboard</button></p></form><p id="lerr" class="err"></p></div>
<div id="app" hidden><header><h1>📊 {{BOT}} · Admin</h1><div id="ranges"></div></header><main>
<div class="grid" id="cards"></div><img id="c_users" alt="Users chart"><img id="c_jobs" alt="Jobs chart">
<div class="card scroll"><table id="tbl"></table></div><p class="k" id="upd"></p></main></div>
<script>
let token=sessionStorage.getItem('vt')||'',days=30;
if(location.hash.length>1){token=decodeURIComponent(location.hash.slice(1));sessionStorage.setItem('vt',token);history.replaceState(null,'',location.pathname)}
function save(t){token=t;sessionStorage.setItem('vt',t);load()}
async function get(u){const r=await fetch(u,{headers:{'X-Admin-Token':token},cache:'no-store'});if(!r.ok)throw new Error(r.status+' '+await r.text());return r}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function sum(a){return a.reduce((x,y)=>x+y,0)}
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
  const keys=['active','new','encode','rename','save','leech','tool','clone','stars'];
  let h='<tr><th>Day</th>'+keys.map(k=>'<th>'+esc(L[k])+'</th>').join('')+'</tr>';
  for(let i=s.days.length-1;i>=0;i--)h+='<tr><td>'+s.days[i]+'</td>'+keys.map(k=>'<td>'+s[k][i]+'</td>').join('')+'</tr>';
  document.getElementById('tbl').innerHTML=h;
  document.getElementById('ranges').innerHTML=[7,30,90].map(n=>'<button class="'+(n==days?'on':'')+'" onclick="days='+n+';load()">'+n+' days</button>').join(' ');
  document.getElementById('upd').textContent='Updated '+new Date().toLocaleString();
  img('c_users','users');img('c_jobs','jobs');
 }catch(e){sessionStorage.removeItem('vt');token='';document.getElementById('app').hidden=true;document.getElementById('login').hidden=false;
  document.getElementById('lerr').textContent=String(e.message).startsWith('429')?'Too many attempts – wait 10 minutes.':'Wrong token.'}}
load();setInterval(()=>{if(token&&!document.hidden)load()},60000);
</script></body></html>"""


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", _root)
    app.router.add_get("/health", _health_handler)
    app.router.add_get("/ping", _root)
    app.router.add_get("/admin", _admin_page)
    app.router.add_get("/admin/", _admin_slash)
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
