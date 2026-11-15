"""Offline regression coverage for the /mediainfo public HTTP boundary."""
import asyncio
import socket
from types import SimpleNamespace

import aiohttp
import pytest
from tests.harness import FakeClient, FakeMsg, run
from core import public_http


@pytest.mark.parametrize('url', [
    'http://127.0.0.1/x', 'http://169.254.169.254/latest', 'http://10.0.0.1/x',
    'http://[::1]/', 'http://[::ffff:127.0.0.1]/', 'http://224.0.0.1/',
    'http://127.1/', 'http://0177.0.0.1/', 'http://2130706433/',
    'http://name:secret@example.com/x', 'http://@example.com/x',
    'file:///etc/passwd', 'ftp://example.com/', 'http://example.com:99999/',
    'http://example.com/\nheader', 'http://example.com\\@127.0.0.1/',
    'http://[fe80::1%25eth0]/', 'https://example.com/' + 'a' * 8193,
])
def test_rejects_private_ambiguous_and_credential_urls(url):
    with pytest.raises(public_http.UnsafeMediaURL):
        public_http.validate_url(url)


def test_public_url_retains_query_but_not_fragment():
    assert public_http.validate_url('https://example.com/a?signature=abc#fragment') == 'https://example.com/a?signature=abc'
    assert public_http.validate_url('http://8.8.8.8/file') == 'http://8.8.8.8/file'


class Response:
    def __init__(self, status, location=None):
        self.status = status
        self.headers = {'Location': location} if location else {}
        self.released = False
    def release(self): self.released = True


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)


def test_redirect_validation_happens_before_the_next_request():
    for destination in ('http://127.0.0.1/secret', 'http://169.254.169.254/',
                        'http://127.1/', 'file:///etc/passwd', 'https://user:secret@host/x'):
        first = Response(302, destination); session = Session([first])
        with pytest.raises(public_http.UnsafeMediaURL):
            run(public_http.get_public(session, 'https://example.com/start', headers={'Range':'bytes=0-99'}))
        assert first.released and len(session.calls) == 1
        assert session.calls[0][1]['allow_redirects'] is False


def test_relative_redirects_release_responses_and_preserve_range():
    first, second, last = Response(301, '/next?x=1'), Response(307, 'https://other.example/final'), Response(206)
    session = Session([first, second, last])
    result, url = run(public_http.get_public(session, 'https://example.com/start', headers={'Range':'bytes=0-99'}))
    assert result is last and url == 'https://other.example/final' and first.released and second.released
    assert [x[0] for x in session.calls] == ['https://example.com/start', 'https://example.com/next?x=1', url]
    assert all(x[1] == {'headers': {'Range':'bytes=0-99'}, 'allow_redirects': False} for x in session.calls)
    loop = [Response(302, '/same') for _ in range(3)]; session = Session(loop)
    with pytest.raises(public_http.UnsafeMediaURL):
        run(public_http.get_public(session, 'https://example.com/same', headers={}, max_redirects=2))
    assert len(session.calls) == 3 and all(x.released for x in loop)


def test_dns_rebinding_and_mixed_answers_are_blocked_before_connection():
    async def exercise(hosts):
        hits = []
        async def accept(reader, writer):
            hits.append(True); writer.close(); await writer.wait_closed()
        server = await asyncio.start_server(accept, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        resolver = public_http.PublicResolver()
        await resolver._resolver.close()
        class DNS:
            async def resolve(self, host, port=0, family=socket.AF_UNSPEC):
                return [{'hostname': host, 'host': ip, 'port': port, 'family': socket.AF_INET,
                         'proto': 0, 'flags': 0} for ip in hosts]
            async def close(self): pass
        resolver._resolver = DNS()
        try:
            conn = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False)
            async with aiohttp.ClientSession(connector=conn, trust_env=False) as session:
                with pytest.raises(aiohttp.ClientConnectorError):
                    await public_http.get_public(session, f'http://public.example:{port}/', headers={})
            assert not hits
        finally:
            await resolver.close(); server.close(); await server.wait_closed()
    run(exercise(['127.0.0.1']))
    run(exercise(['8.8.8.8', '127.0.0.1']))


def test_mediainfo_never_exposes_network_exception_url(monkeypatch, caplog):
    from core import tools
    secret = 'https://example.com/video?token=TOP_SECRET'
    async def fail(*args): raise RuntimeError(secret)
    monkeypatch.setattr(tools, '_probe_http_header', fail)
    monkeypatch.setattr(tools.shutil, 'which', lambda _: '/usr/bin/ffprobe')
    msg = FakeMsg('/mediainfo ' + secret, uid=5)
    run(tools.mediainfo_cmd(FakeClient(), msg))
    assert 'Could not read media' in msg.sent[0].edits[-1]
    assert secret not in msg.sent[0].edits[-1] and secret not in caplog.text


def test_ffprobe_does_not_allow_network_protocols(monkeypatch, tmp_path):
    from core import tools
    calls = []
    async def fake_probe(url, path):
        with open(path, 'wb') as f: f.write(b'media')
        return {'file_name': 'video.mp4', 'file_size': 5, 'mime_type': 'video/mp4'}
    class Process:
        async def communicate(self): return b'{"format":{},"streams":[]}', b''
    async def execute(*args, **kwargs): calls.append(args); return Process()
    async def edit(*args, **kwargs): pass
    monkeypatch.setattr(tools, 'TMP', str(tmp_path))
    monkeypatch.setattr(tools.shutil, 'which', lambda _: '/usr/bin/ffprobe')
    monkeypatch.setattr(tools, '_probe_http_header', fake_probe)
    monkeypatch.setattr(tools.asyncio, 'create_subprocess_exec', execute)
    monkeypatch.setattr(tools.rich, 'edit', edit)
    run(tools.mediainfo_cmd(FakeClient(), FakeMsg('/mediainfo https://example.com/video.mp4', uid=5)))
    assert len(calls) == 1 and calls[0][calls[0].index('-protocol_whitelist') + 1] == 'file,pipe'


def test_real_http_header_fetch_follows_checked_redirect_and_caps_bytes(monkeypatch, tmp_path):
    # This is an isolated localhost fixture: its resolver is deliberately
    # replaced only in this test so the real HTTP client can reach it.
    from core import public_http as safe
    from core.tools import _probe_http_header
    seen = []
    async def handle(reader, writer):
        request = await reader.readuntil(b'\r\n\r\n')
        seen.append(request.decode('latin1'))
        if request.startswith(b'GET /start'):
            writer.write(b'HTTP/1.1 302 Found\r\nLocation: /video?signature=ok\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
        else:
            writer.write(b'HTTP/1.1 206 Partial Content\r\nContent-Type: video/mp4\r\nContent-Range: bytes 0-3/100\r\nContent-Length: 10\r\nConnection: close\r\n\r\n0123456789')
        await writer.drain(); writer.close(); await writer.wait_closed()
    class TestResolver(aiohttp.abc.AbstractResolver):
        async def resolve(self, host, port=0, family=socket.AF_UNSPEC):
            return [{'hostname': host, 'host': '127.0.0.1', 'port': port,
                     'family': socket.AF_INET, 'proto': 0, 'flags': 0}]
        async def close(self): pass
    async def exercise():
        server = await asyncio.start_server(handle, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        try:
            result = await _probe_http_header(f'http://media.example:{port}/start', str(tmp_path / 'header'), max_bytes=4)
        finally:
            server.close(); await server.wait_closed()
        assert result['file_size'] == 100 and result['mime_type'] == 'video/mp4'
        assert (tmp_path / 'header').read_bytes() == b'0123'
    monkeypatch.setattr(safe, 'PublicResolver', TestResolver)
    run(exercise())
    assert len(seen) == 2 and seen[1].startswith('GET /video?signature=ok ')
    assert all('Range: bytes=0-3' in request for request in seen)
