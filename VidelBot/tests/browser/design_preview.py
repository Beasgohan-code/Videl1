"""Isolated local visual test: synthetic read-only data, no production or DB access."""
import os, sys
from pathlib import Path
os.environ.update(API_ID='1', API_HASH='preview', BOT_TOKEN='123456:PREVIEW', DB_URI='mongodb://localhost:27017', OWNER_ID='111', ADMIN_WEB_TOKEN='')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from aiohttp import web
from webapp.public_site import render_public_html
from webapp.ui_shell import render_app_html
from webapp.ui_styles import STYLES_CSS
from webapp.ui_client import CLIENT_JS
from webapp.ui_motion import MOTION_JS

BANNER = '<div style="padding:8px 16px;background:#dfe6ff;color:#1d2d66;text-align:center;font:600 12px system-ui">LOCAL DESIGN PREVIEW · Synthetic sample data only · No accounts or live backend · All changes disabled</div>'
SEED = '<script>sessionStorage.setItem("videl_saas_tok","local-synthetic-preview");sessionStorage.setItem("videl_saas_csrf","preview");</script>'
WS={'workspace_id':'preview','name':'Northstar workspace','icon':'✦','description':'A synthetic workspace for local design review.'}
BOTS=[{'bot_id':'sample1','telegram_bot_id':101,'display_name':'Archive assistant','username':'sample_archive','runtime_running':True,'has_credentials':True,'connection_state':'running','category':'File Store','config_version':3,'description':'An example file archive bot for this local preview.','tags':['archive']}, {'bot_id':'sample2','telegram_bot_id':102,'display_name':'Media assistant','username':'sample_media','runtime_running':False,'has_credentials':True,'connection_state':'credentials_verified_idle','category':'Media','config_version':2,'description':'A second synthetic bot record.','tags':['media']}]
BOTS[0].update({'features':{'filestore':True,'custom_caption':True,'auto_delete':False},'settings':{'welcome_message':'Welcome to the archive assistant.','custom_caption':'File: {name}','auto_delete_seconds':0}})
REVISIONS=[{'version':3,'status':'applied','note':'Updated welcome message'},{'version':2,'status':'draft','note':'Caption experiment'},{'version':1,'status':'applied','note':'Initial configuration'}]
ENVIRONMENTS=[{'env':'development','label':'Development','features':{'filestore':True,'custom_caption':True}},{'env':'testing','label':'Testing','features':{'filestore':True}},{'env':'production','label':'Production','features':{'filestore':True}}]
FILES=[{'file_uid':'fsf_sample1','filename':'archive-guide.pdf','category':'document','size_bytes':2758700,'deleted':False,'uploaded_at':'2026-11-01T00:00:00Z'}, {'file_uid':'fsf_sample2','filename':'welcome-banner.png','category':'image','size_bytes':482304,'deleted':False,'uploaded_at':'2026-11-02T00:00:00Z'}, {'file_uid':'fsf_sample3','filename':'old-notes.txt','category':'document','size_bytes':2048,'deleted':True,'uploaded_at':'2026-10-30T00:00:00Z'}]
FILE_POLICY={'max_file_mb':2000,'duplicate_policy':'warn','caption_template':'{filename} · {filesize}'}
COMMANDS=[{'command':'start','description':'Open the bot home screen','category':'General','permission':'everyone','enabled':True,'handler_bound':True},{'command':'helpdesk','description':'Example command metadata for this preview','category':'Custom','permission':'owner','enabled':False,'handler_bound':False}]
DETAIL={'workspace':WS,'my_role':'owner','my_permissions':{'bot_settings':True,'commands':True,'cloning':True,'automation':True,'filestore':True,'logs':True,'integrations':True,'analytics':True,'templates':True,'team_management':True},'usage':{'bots_count':2,'files_count':28,'storage_bytes':482344960,'filestore_sample_limit':10000,'unread_notifications':2},'recent_notifications':[{'title':'Example notification','message':'Sample data for this visual preview.'},{'title':'Local preview','message':'No production services are connected.'}]}
def page(html, seed=False):
    html=html.replace('<body>','<body>'+BANNER+(SEED if seed else ''),1)
    return html.replace('<script id="telegram-web-app" src="https://telegram.org/js/telegram-web-app.js" async></script>','')
async def home(r): return web.Response(text=page(render_public_html('Videl')),content_type='text/html',headers={'Cache-Control':'no-store'})
async def studio(r): return web.Response(text=page(render_app_html(),True),content_type='text/html',headers={'Cache-Control':'no-store'})
async def css(r): return web.Response(text=STYLES_CSS,content_type='text/css')
async def js(r): return web.Response(text=CLIENT_JS,content_type='application/javascript')
async def motion(r): return web.Response(text=MOTION_JS,content_type='application/javascript')
async def vendor(r):
    name=r.match_info['name']
    if name not in ('gsap-3.13.0.min.js','mojs-1.7.1.min.js','three-0.160.1.min.js'): raise web.HTTPNotFound()
    return web.FileResponse(Path(__file__).resolve().parents[2]/'frontend'/'assets'/'vendor'/name)
async def api(r):
    if r.method!='GET': return web.json_response({'error':'This local preview is read-only. No backend is connected.'},status=503)
    route=r.path
    if route == '/api/v1/workspaces/preview/search':
        q=r.query.get('q','').lower()
        synthetic=[{'type':'bot','id':'sample1','title':'Archive assistant','subtitle':'Synthetic bot','studio':'bots'}, {'type':'bot','id':'sample2','title':'Media assistant','subtitle':'Synthetic bot','studio':'bots'}, {'type':'command','id':'helpdesk','bot_id':'sample1','title':'/helpdesk','subtitle':'Archive assistant','studio':'commands'}]
        return web.json_response({'results':[item for item in synthetic if q and q in (item['title']+' '+item['subtitle']).lower()]})
    if route == '/api/v1/workspaces/preview/filestore':
        q=r.query.get('search','').lower()
        rows=[f for f in FILES if q in (f['filename']+' '+f['file_uid']).lower()]
        return web.json_response({'items':rows,'total':len(rows),'total_bytes':sum(f['size_bytes'] for f in rows),'page':1,'pages':1,'page_size':25,'sample_limit':2000,'sample_truncated':False})
    data={'/api/v1/auth/me':{'user':{'user_id':1,'is_platform_admin':False},'workspaces':[WS],'catalogs':{},'settings':{}},'/api/v1/workspaces/preview/bots':{'bots':BOTS},'/api/v1/workspaces/preview':DETAIL,'/api/v1/workspaces/preview/filestore/config':FILE_POLICY,'/api/v1/workspaces/preview/fleet':{'bots':BOTS,'total_bots':2,'state_counts':{'running':1,'credentials_verified_idle':1,'disconnected':0,'archived':0},'config_drift_fields':['custom_caption']},'/api/v1/workspaces/preview/health':{'health':{'architecture_note':'Sample process-local observations in this isolated preview.','alerts':[{'severity':'info','message':'Sample saved credentials; worker not observed running here.'}]},'webhooks':{'webhooks':[]}},'/api/v1/workspaces/preview/bots/sample1/commands':{'commands':COMMANDS},'/api/v1/workspaces/preview/bots/sample1':{'bot':BOTS[0]},'/api/v1/workspaces/preview/bots/sample1/config':{'revisions':REVISIONS},'/api/v1/workspaces/preview/bots/sample1/environments':{'environments':ENVIRONMENTS}}
    if route in data: return web.json_response(data[route])
    return web.json_response({'error':'This local preview contains synthetic overview data only. This page has no backend.'},status=503)
app=web.Application(); app.router.add_get('/',home);app.router.add_get('/app',studio);app.router.add_get('/app/',studio)
app.router.add_get('/app/styles.css',css);app.router.add_get('/app/app.js',js);app.router.add_get('/app/motion.js',motion);app.router.add_get('/app/vendor/{name}',vendor)
app.router.add_route('*','/api/{tail:.*}',api);app.router.add_route('*','/admin{tail:.*}',api)
web.run_app(app,host='0.0.0.0',port=8082,access_log=None)
