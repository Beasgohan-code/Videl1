"""Isolated synthetic Next preview API. Never connect this fixture to real users or data."""
import asyncio
import secrets
from aiohttp import web

sessions = {}
records = {}
commands = {}
files = {}
templates = {}
notifications = {}
teams = {}
roles = {}
analytics_access = {}
personal_settings = {}


def error(message, status):
    return web.json_response({'error': message}, status=status)


def account(request):
    token = request.headers.get('Authorization', '').removeprefix('Bearer ')
    return sessions.get(token)


async def login(request):
    body = await request.json()
    if body.get('link') not in ('l.valid', 'l.empty', 'l.readonly', 'l.noanalytics') and body.get('init_data') not in ('synthetic-signed-launch', 'auth_date=101&hash=synthetic'):
        return error('Sign-in link expired or already used.', 401)
    token = secrets.token_urlsafe(18)
    sessions[token] = [] if body.get('link') == 'l.empty' else [
        {'workspace_id': 'slow', 'name': 'Slow workspace'},
        {'workspace_id': 'bravo', 'name': 'Bravo workspace'},
    ]
    personal_settings[token] = {"appearance": {"theme": "dark", "file_view": "list"}, "general": {"compact_tables": False}}
    records[token] = {}
    commands[token] = {}
    files[token] = {}
    templates[token] = {}
    notifications[token] = {}
    teams[token] = {}
    roles[token] = body.get('link') not in ('l.readonly', 'l.noanalytics')
    analytics_access[token] = body.get('link') != 'l.noanalytics'
    return web.json_response({'session_token': token, 'csrf_token': 'fixture-csrf'})


async def me(request):
    workspaces = account(request)
    if workspaces is None:
        return error('Unauthorized', 401)
    return web.json_response({'user': {'user_id':1,'display_name': 'Fixture user', 'is_platform_admin': False, 'csrf_token': 'fixture-csrf'}, 'workspaces': workspaces, 'catalogs': {'workspace_roles':['owner','administrator','manager','developer','analyst','viewer'],'permission_scopes':['team_management','analytics']}})


async def logout(request):
    token = request.headers.get('Authorization', '').removeprefix('Bearer ')
    if token not in sessions:
        return error('Unauthorized', 401)
    if request.headers.get('X-CSRF-Token') != 'fixture-csrf':
        return error('CSRF required', 403)
    del sessions[token]
    records.pop(token, None); commands.pop(token, None); files.pop(token, None); templates.pop(token, None); notifications.pop(token, None); teams.pop(token, None); roles.pop(token, None); analytics_access.pop(token, None); personal_settings.pop(token, None)
    return web.json_response({'logged_out': True})


async def create(request):
    workspaces = account(request)
    if workspaces is None:
        return error('Unauthorized', 401)
    if request.headers.get('X-CSRF-Token') != 'fixture-csrf':
        return error('CSRF required', 403)
    body = await request.json()
    name = str(body.get('name', '')).strip()
    if not 2 <= len(name) <= 80:
        return error('Invalid name', 400)
    created = {'workspace_id': 'new_' + secrets.token_hex(4), 'name': name}
    workspaces.append(created)
    return web.json_response({'workspace': created}, status=201)


async def workspace(request):
    workspaces = account(request)
    if workspaces is None:
        return error('Unauthorized', 401)
    ws = next((w for w in workspaces if w['workspace_id'] == request.match_info['ws']), None)
    if ws is None:
        return error('Forbidden', 403)
    if ws['workspace_id'] == 'slow':
        await asyncio.sleep(1.1)
    token = request.headers['Authorization'].removeprefix('Bearer ')
    # A delayed workspace response can resume after the synthetic session logs out.
    if token not in sessions:
        return error('Unauthorized', 401)
    if request.path.endswith('/bots'):
        return web.json_response({'bots': bot_records(token, ws)})
    return web.json_response({'workspace': ws, 'my_role': 'owner' if roles[token] else 'viewer', 'my_permissions': {'bot_settings': roles[token], 'commands': roles[token], 'filestore': roles[token], 'templates': roles[token], 'logs': roles[token], 'team_management': roles[token], 'analytics': analytics_access[token]}})



def bot_records(token, ws):
    return records[token].setdefault(ws['workspace_id'], [{'bot_id': ws['workspace_id'] + '_bot', 'display_name': ws['name'] + ' bot', 'category': 'Utility', 'tags': ['alpha', 'beta'], 'connection_state': 'disconnected', 'archived': False}])


def authorized(request, *, write=False):
    token = request.headers.get('Authorization', '').removeprefix('Bearer ')
    if token not in sessions:
        return None, error('Unauthorized', 401)
    ws = next((x for x in sessions[token] if x['workspace_id'] == request.match_info['ws']), None)
    if ws is None or (write and not roles[token]):
        return None, error('Forbidden', 403)
    if write and request.headers.get('X-CSRF-Token') != 'fixture-csrf':
        return None, error('CSRF required', 403)
    return (token, ws), None


async def verify_token(request):
    context, failure = authorized(request, write=True)
    if failure is not None: return failure
    data = await request.json()
    return web.json_response({'verified_with_telegram': data.get('bot_token') == 'fixture-token'})


async def create_bot(request):
    context, failure = authorized(request, write=True)
    if failure is not None: return failure
    token, ws = context
    data = await request.json()
    bot = {'bot_id': 'bot_' + secrets.token_hex(4), 'display_name': data.get('display_name', ''), 'description': data.get('description', ''), 'connection_state':'disconnected', 'archived':False}
    bot_records(token, ws).append(bot)
    return web.json_response({'bot': bot}, status=201)


async def bot_item(request):
    context, failure = authorized(request, write=request.method != 'GET')
    if failure is not None: return failure
    token, ws = context
    bots = bot_records(token, ws)
    bot = next((b for b in bots if b['bot_id'] == request.match_info['bot']), None)
    if bot is None: return error('Bot not found', 404)
    if request.method == 'GET': return web.json_response({'bot': bot, 'capabilities': {'conditional_profile_update': True, 'conditional_profile_fields':['display_name','description','category','tags']}})
    if request.method == 'DELETE':
        bots.remove(bot); return web.json_response({'deleted': True})
    data = await request.json()
    for field in ('display_name', 'description'):
        if field in data: bot[field] = data[field].strip()
    if 'archived' in data:
        bot['archived'] = data['archived'] is True
        bot['connection_state'] = 'archived' if bot['archived'] else 'disconnected'
    return web.json_response({'bot': bot})


async def bot_profile(request):
    context, failure = authorized(request, write=True)
    if failure is not None: return failure
    token, ws = context
    bot = next((b for b in bot_records(token, ws) if b['bot_id'] == request.match_info['bot']), None)
    data = await request.json()
    expected = data.get('expected', {})
    keys = set(data) - {'expected'}
    if not keys or not keys.issubset({'display_name', 'description', 'category', 'tags'}) or not isinstance(expected, dict) or set(expected) != keys:
        return error('Invalid conditional update', 400)
    if not bot or any((bot.get(k) or ([] if k=='tags' else '')) != expected[k] for k in keys):
        return web.json_response({'error':'The record changed or is no longer available.', 'code':'profile_conflict'}, status=409)
    for field in keys: bot[field] = [t.strip() for t in data[field]] if field=='tags' else data[field].strip()
    return web.json_response({'bot': bot})


async def command_collection(request):
    context, failure = authorized(request, write=request.method != 'GET')
    if failure is not None: return failure
    token, ws = context
    if not roles[token]: return error('Forbidden', 403)
    bot = next((b for b in bot_records(token, ws) if b['bot_id'] == request.match_info['bot']), None)
    if bot is None: return error('Bot not found', 404)
    rows = commands[token].setdefault((ws['workspace_id'], bot['bot_id']), [])
    if request.method == 'GET': return web.json_response({'commands': rows, 'capabilities': {'command_create_only': True}})
    data = await request.json()
    old = next((c for c in rows if c['command'] == data.get('command')), None)
    if request.method == 'DELETE':
        if old: rows.remove(old)
        return web.json_response({'deleted': bool(old)})
    if old and data.get('create_only'): return error('Command already exists.', 400)
    if old: rows.remove(old)
    entry = {'command': data['command'], 'description': data['description'], 'category': data['category'], 'permission': data['permission'], 'visibility':data['visibility'], 'enabled':data['enabled'], 'custom_reply_text':data.get('custom_reply_text', ''), 'handler_bound': False}
    rows.append(entry)
    return web.json_response({'command':entry, 'commands':rows})


async def command_create(request):
    context, failure = authorized(request, write=True)
    if failure is not None: return failure
    token, ws = context
    if not roles[token]: return error('Forbidden', 403)
    bot = next((b for b in bot_records(token, ws) if b['bot_id'] == request.match_info['bot']), None)
    if bot is None: return error('Bot not found', 404)
    rows = commands[token].setdefault((ws['workspace_id'], bot['bot_id']), [])
    data = await request.json()
    if any(c['command'] == data.get('command') for c in rows):
        return web.json_response({'error':'A command with this name already exists.', 'code':'command_exists'},status=409)
    entry = {**data, 'custom_reply_text':'', 'handler_bound':False}
    rows.append(entry)
    return web.json_response({'command':entry},status=201)



async def file_collection(request):
    context, failure = authorized(request, write=request.method != 'GET')
    if failure is not None: return failure
    token, ws = context
    if not roles[token]: return error('Forbidden', 403)
    rows = files[token].setdefault(ws['workspace_id'], [])
    if request.method == 'GET':
        term = request.query.get('search', '').lower()
        category = request.query.get('category', 'all')
        include_deleted = request.query.get('include_deleted') in ('1', 'true')
        found = [f for f in rows if term in f['filename'].lower() and (category == 'all' or f['category'] == category) and (include_deleted or not f['deleted'])]
        return web.json_response({'items':found[:25], 'total':len(found), 'total_bytes':sum(x['size_bytes'] for x in found), 'page':1, 'pages':1, 'sample_limit':2000, 'sample_truncated':False})
    reader = await request.multipart()
    while part := await reader.next():
        if part.filename:
            content = await part.read()
            rows.append({'file_uid':'file_'+secrets.token_hex(4),'filename':part.filename,'size_bytes':len(content),'category':('document' if part.filename.endswith('.txt') else 'image' if part.filename.endswith('.png') else 'other'),'deleted':False})
    return web.json_response({'uploaded': rows},status=201)


async def file_item(request):
    context, failure = authorized(request, write=True)
    if failure is not None: return failure
    token, ws = context
    rows = files[token].setdefault(ws['workspace_id'], [])
    record = next((f for f in rows if f['file_uid'] == request.match_info['file']), None)
    if record is None: return error('File not found', 404)
    data = await request.json()
    action = data.get('action')
    if action == 'rename': record['filename'] = data.get('new_name', '')
    elif action == 'restore': record['deleted'] = False
    elif action == 'soft_delete' and data.get('confirmed') is True: record['deleted'] = True
    elif action == 'purge' and data.get('confirmed') is True and record['deleted']: rows.remove(record)
    else: return error('Invalid action', 400)
    return web.json_response({'action':action})


async def settings(request):
    token = request.headers.get('Authorization', '').removeprefix('Bearer ')
    if token not in sessions: return error('Unauthorized', 401)
    if request.method == 'POST':
        if request.headers.get('X-CSRF-Token') != 'fixture-csrf': return error('CSRF required', 403)
        patch = await request.json()
        for section in ('appearance', 'general'):
            if section in patch: personal_settings[token][section].update(patch[section])
        return web.json_response({'settings': personal_settings[token]})
    return web.json_response({'settings': personal_settings[token], 'capabilities': {'atomic_field_updates': True}})


async def old_app(_request):
    return web.Response(text='Synthetic fixture: existing Studio is not served here.', content_type='text/plain')


app = web.Application()
app.router.add_post('/api/v1/auth/login', login)
app.router.add_get('/api/v1/auth/me', me)
app.router.add_post('/api/v1/auth/logout', logout)
app.router.add_route('*', '/api/v1/settings', settings)
app.router.add_post('/api/v1/workspaces', create)
app.router.add_get('/api/v1/workspaces/{ws}', workspace)
app.router.add_get('/api/v1/workspaces/{ws}/bots', workspace)
app.router.add_post('/api/v1/workspaces/{ws}/bots/verify-token', verify_token)
app.router.add_post('/api/v1/workspaces/{ws}/bots', create_bot)
app.router.add_get('/api/v1/workspaces/{ws}/bots/{bot}', bot_item)
app.router.add_patch('/api/v1/workspaces/{ws}/bots/{bot}/profile', bot_profile)
app.router.add_patch('/api/v1/workspaces/{ws}/bots/{bot}', bot_item)
app.router.add_delete('/api/v1/workspaces/{ws}/bots/{bot}', bot_item)
app.router.add_route('*', '/api/v1/workspaces/{ws}/bots/{bot}/commands', command_collection)
app.router.add_post('/api/v1/workspaces/{ws}/bots/{bot}/commands/create', command_create)
app.router.add_get('/api/v1/workspaces/{ws}/filestore', file_collection)
app.router.add_post('/api/v1/workspaces/{ws}/filestore', file_collection)
app.router.add_post('/api/v1/workspaces/{ws}/filestore/{file}', file_item)


async def fleet(request):
    context, failure = authorized(request)
    if failure is not None: return failure
    token, ws = context
    bots = bot_records(token, ws)
    return web.json_response({'total_bots': len(bots), 'state_counts': {'running': 0}, 'bots': bots})


async def template_collection(request):
    context, failure = authorized(request, write=request.method != 'GET')
    if failure is not None: return failure
    token, ws = context
    if not roles[token]: return error('Forbidden', 403)
    rows = templates[token].setdefault(ws['workspace_id'], [])
    if request.method == 'GET':
        return web.json_response({'official':[{'template_id':'official_1','name':'Official example','category':'bot_config','description':'Fixture example','version':'1.0','payload':{'example':True}}], 'workspace_templates':rows})
    data = await request.json()
    if data.get('action') == 'delete':
        row = next((x for x in rows if x['template_id'] == data.get('template_id')), None)
        if row: rows.remove(row)
        return web.json_response({'deleted':bool(row)})
    if data.get('create_only') is not True or data.get('template_id'): return error('Create only required', 400)
    row = {'template_id':'fixture_'+secrets.token_hex(4),'workspace_id':ws['workspace_id'],'name':data['name'],'category':data['category'],'description':data['description'],'payload':data['payload'],'version':'1.0.0'}
    rows.append(row)
    return web.json_response({'template':row})


async def notification_collection(request):
    context, failure = authorized(request, write=request.method != 'GET')
    if failure is not None: return failure
    token, ws = context
    if not roles[token]: return error('Forbidden', 403)
    rows = notifications[token].setdefault(ws['workspace_id'], [
        {'notification_id':'notice_1','title':ws['name']+' alert','message':'Synthetic alert','category':'bots','severity':'info','read':False,'created_at':'2026-01-01'},
        {'notification_id':'notice_2','title':'Second alert','message':'Synthetic message','category':'storage','severity':'warning','read':False,'created_at':'2026-01-02'},
    ])
    if request.method == 'GET': return web.json_response({'notifications':rows, 'activity_logs':[], 'activity_logs_availability':'unavailable'})
    data = await request.json()
    if data.get('mark_all_read') is True:
        for row in rows: row['read'] = True
        return web.json_response({'updated':True})
    row = next((x for x in rows if x['notification_id'] == data.get('notification_id')), None)
    if not row or not isinstance(data.get('read'), bool): return web.json_response({'updated':False})
    row['read'] = data['read']
    return web.json_response({'updated':True})



TEAM_DEFAULTS = {'owner':{'team_management':True,'analytics':True},'administrator':{'team_management':True,'analytics':True},'manager':{'team_management':False,'analytics':True},'developer':{'team_management':False,'analytics':True},'analyst':{'team_management':False,'analytics':True},'viewer':{'team_management':False,'analytics':True}}

async def team_collection(request):
    context, failure = authorized(request, write=request.method != 'GET')
    if failure is not None: return failure
    token, ws = context
    if not roles[token]: return error('Forbidden', 403)
    members = teams[token].setdefault(ws['workspace_id'], [{'user_id':1,'role':'owner','permissions':TEAM_DEFAULTS['owner'].copy()}])
    if request.method == 'GET': return web.json_response({'members':members,'role_defaults':TEAM_DEFAULTS,'reviewed_membership_guards':True})
    data=await request.json()
    if data.get('role') not in TEAM_DEFAULTS or data.get('role') == 'owner' or not isinstance(data.get('user_id'),int) or any(m['user_id']==data['user_id'] for m in members): return error('Invalid member',400)
    member={'user_id':data['user_id'],'role':data['role'],'permissions':data['permissions']}
    members.append(member)
    return web.json_response({'member':member},status=201)

async def team_member(request):
    context, failure = authorized(request, write=True)
    if failure is not None: return failure
    token, ws = context
    members=teams[token].setdefault(ws['workspace_id'], [])
    member=next((m for m in members if str(m['user_id'])==request.match_info['member']),None)
    data=await request.json()
    if not member or member['role']=='owner': return error('Forbidden',403)
    if data.get('expected_role')!=member['role'] or data.get('expected_permissions')!=member['permissions']: return error('Member changed; reload.',400)
    if request.method=='DELETE':
        members.remove(member); return web.json_response({'removed':True})
    member['role']=data['role'];member['permissions']=data['permissions']
    return web.json_response({'member':member})

app.router.add_route('*', '/api/v1/workspaces/{ws}/team', team_collection)
app.router.add_route('*', '/api/v1/workspaces/{ws}/team/{member}', team_member)


async def analytics(request):
    context, failure = authorized(request)
    if failure is not None: return failure
    token, ws = context
    if not analytics_access[token]: return error('Forbidden', 403)
    days=int(request.query.get('days','30'))
    bot=request.query.get('bot_id','all')
    if request.path.endswith('.csv'):
        return web.Response(text='date,uploads,downloads,storage_bytes_added\n2026-10-01,2,,1024\n',content_type='text/csv')
    return web.json_response({'range_days':days,'bot_id':bot,'totals':{'stored_files':(42 if ws['workspace_id']=='slow' else 84) if bot=='all' else 5,'storage_bytes':2048,'period_uploads':2},'comparison_previous_period':{'files_delta':1},'series':{'days':['2026-10-01','2026-10-02'],'uploads':[2,0],'storage_bytes_added':[1024,0]},'file_sample_truncated':days==90})

app.router.add_get('/api/v1/workspaces/{ws}/analytics', analytics)
app.router.add_get('/api/v1/workspaces/{ws}/analytics.csv', analytics)
app.router.add_get('/api/v1/workspaces/{ws}/fleet', fleet)
app.router.add_route('*', '/api/v1/workspaces/{ws}/templates', template_collection)
app.router.add_route('*', '/api/v1/workspaces/{ws}/notifications', notification_collection)
app.router.add_get('/app', old_app)
if __name__ == '__main__':
    web.run_app(app, host='127.0.0.1', port=8084, access_log=None)
