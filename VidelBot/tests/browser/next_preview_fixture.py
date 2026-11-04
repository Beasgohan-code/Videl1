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
roles = {}


def error(message, status):
    return web.json_response({'error': message}, status=status)


def account(request):
    token = request.headers.get('Authorization', '').removeprefix('Bearer ')
    return sessions.get(token)


async def login(request):
    body = await request.json()
    if body.get('link') not in ('l.valid', 'l.empty', 'l.readonly'):
        return error('Sign-in link expired or already used.', 401)
    token = secrets.token_urlsafe(18)
    sessions[token] = [] if body['link'] == 'l.empty' else [
        {'workspace_id': 'slow', 'name': 'Slow workspace'},
        {'workspace_id': 'bravo', 'name': 'Bravo workspace'},
    ]
    records[token] = {}
    commands[token] = {}
    files[token] = {}
    templates[token] = {}
    notifications[token] = {}
    roles[token] = body['link'] != 'l.readonly'
    return web.json_response({'session_token': token, 'csrf_token': 'fixture-csrf'})


async def me(request):
    workspaces = account(request)
    if workspaces is None:
        return error('Unauthorized', 401)
    return web.json_response({'user': {'display_name': 'Fixture user', 'is_platform_admin': False, 'csrf_token': 'fixture-csrf'}, 'workspaces': workspaces})


async def logout(request):
    token = request.headers.get('Authorization', '').removeprefix('Bearer ')
    if token not in sessions:
        return error('Unauthorized', 401)
    if request.headers.get('X-CSRF-Token') != 'fixture-csrf':
        return error('CSRF required', 403)
    del sessions[token]
    records.pop(token, None); commands.pop(token, None); files.pop(token, None); templates.pop(token, None); notifications.pop(token, None); roles.pop(token, None)
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
    if request.path.endswith('/bots'):
        return web.json_response({'bots': bot_records(token, ws)})
    return web.json_response({'workspace': ws, 'my_role': 'owner' if roles[token] else 'viewer', 'my_permissions': {'bot_settings': roles[token], 'commands': roles[token], 'filestore': roles[token], 'templates': roles[token], 'logs': roles[token]}})



def bot_records(token, ws):
    return records[token].setdefault(ws['workspace_id'], [{'bot_id': ws['workspace_id'] + '_bot', 'display_name': ws['name'] + ' bot', 'connection_state': 'disconnected', 'archived': False}])


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
    context, failure = authorized(request, write=True)
    if failure is not None: return failure
    token, ws = context
    bots = bot_records(token, ws)
    bot = next((b for b in bots if b['bot_id'] == request.match_info['bot']), None)
    if bot is None: return error('Bot not found', 404)
    if request.method == 'DELETE':
        bots.remove(bot); return web.json_response({'deleted': True})
    data = await request.json()
    bot['archived'] = data.get('archived') is True
    bot['connection_state'] = 'archived' if bot['archived'] else 'disconnected'
    return web.json_response({'bot': bot})


async def command_collection(request):
    context, failure = authorized(request, write=request.method != 'GET')
    if failure is not None: return failure
    token, ws = context
    if not roles[token]: return error('Forbidden', 403)
    bot = next((b for b in bot_records(token, ws) if b['bot_id'] == request.match_info['bot']), None)
    if bot is None: return error('Bot not found', 404)
    rows = commands[token].setdefault((ws['workspace_id'], bot['bot_id']), [])
    if request.method == 'GET': return web.json_response({'commands': rows})
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



async def file_collection(request):
    context, failure = authorized(request, write=request.method != 'GET')
    if failure is not None: return failure
    token, ws = context
    if not roles[token]: return error('Forbidden', 403)
    rows = files[token].setdefault(ws['workspace_id'], [])
    if request.method == 'GET':
        term = request.query.get('search', '').lower()
        found = [f for f in rows if term in f['filename'].lower()]
        return web.json_response({'items':found[:25], 'total':len(found), 'total_bytes':sum(x['size_bytes'] for x in found), 'page':1, 'pages':1, 'sample_limit':2000, 'sample_truncated':False})
    reader = await request.multipart()
    while part := await reader.next():
        if part.filename:
            content = await part.read()
            rows.append({'file_uid':'file_'+secrets.token_hex(4),'filename':part.filename,'size_bytes':len(content),'category':'other','deleted':False})
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


async def old_app(_request):
    return web.Response(text='Synthetic fixture: existing Studio is not served here.', content_type='text/plain')


app = web.Application()
app.router.add_post('/api/v1/auth/login', login)
app.router.add_get('/api/v1/auth/me', me)
app.router.add_post('/api/v1/auth/logout', logout)
app.router.add_post('/api/v1/workspaces', create)
app.router.add_get('/api/v1/workspaces/{ws}', workspace)
app.router.add_get('/api/v1/workspaces/{ws}/bots', workspace)
app.router.add_post('/api/v1/workspaces/{ws}/bots/verify-token', verify_token)
app.router.add_post('/api/v1/workspaces/{ws}/bots', create_bot)
app.router.add_patch('/api/v1/workspaces/{ws}/bots/{bot}', bot_item)
app.router.add_delete('/api/v1/workspaces/{ws}/bots/{bot}', bot_item)
app.router.add_route('*', '/api/v1/workspaces/{ws}/bots/{bot}/commands', command_collection)
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

app.router.add_get('/api/v1/workspaces/{ws}/fleet', fleet)
app.router.add_route('*', '/api/v1/workspaces/{ws}/templates', template_collection)
app.router.add_route('*', '/api/v1/workspaces/{ws}/notifications', notification_collection)
app.router.add_get('/app', old_app)
if __name__ == '__main__':
    web.run_app(app, host='127.0.0.1', port=8084, access_log=None)
