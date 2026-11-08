"""Offline tests: chat game, beta transport gate and plugin wiring."""
from types import SimpleNamespace
import pytest
from tests.harness import FakeClient, FakeMsg, FakeQuery, load_all, no_botapi, run
load_all()

@pytest.fixture(autouse=True)
def setup(monkeypatch):
    from core.plugin_manager import plugin_manager
    plugin_manager._enabled.clear()
    plugin_manager.discover()
    monkeypatch.delenv("GAME_WIDGETS_BETA", raising=False)
    no_botapi()
    yield
    plugin_manager._enabled.clear()
    no_botapi()

def test_board_complete_win_loss_draw_and_block():
    from plugins.game.plugin import move, winner
    assert winner("XXXOO----") == "X"
    assert winner("O-X-O-X-O") == "O"
    assert not winner("XOXOXXOXO")
    assert move("XX-OO----", 2) == "XXXOO----"
    assert winner(move("OO-XX----", 5)) == "X"
    assert move("O--XXO---", 6)[2] == "O"
    assert move("OO-XX----", 6)[2] == "O"
    assert move("XOXOXXO--", 8) == "XOXOXXO-X"
    assert move("XXXOO----", 5) == "XXXOO----"

def test_buttons_are_compact_and_command_routes_to_fallback():
    from plugins.game import plugin as game
    from core.commands import USER_COMMANDS, GROUP_COMMANDS, ADMIN_COMMANDS, OWNER_COMMANDS
    c, m = FakeClient(), FakeMsg('/game', uid=77)
    captured = {}
    async def reply(text, **kwargs):
        captured.update(text=text, **kwargs)
    m.reply_text = reply
    run(game.game_command(c, m))
    assert 'Tic-tac-toe' in captured['text']
    rows = captured['reply_markup'].inline_keyboard
    assert len(rows) == 5 and len(rows[0]) == 3
    assert all(len(b.callback_data.encode()) <= 64 for row in rows for b in row)
    assert any(name == 'game' for name, _ in USER_COMMANDS)
    assert any(name == 'game' for name, _ in GROUP_COMMANDS)
    assert len({name for group in (USER_COMMANDS, ADMIN_COMMANDS, OWNER_COMMANDS) for name, _ in group}) <= 100
    assert 'sh' not in {name for name, _ in OWNER_COMMANDS}

def test_callback_ownership_moves_restart_bad_data_and_disabled():
    from plugins.game import plugin as game
    from core.plugin_manager import plugin_manager
    c, msg = FakeClient(), FakeMsg('board')
    q = FakeQuery('vga:77:---------:4', uid=78, message=msg)
    run(game.game_callback(c, q))
    assert q.answers[-1][1] and not msg.edits
    q = FakeQuery('vga:77:---------:4', uid=77, message=msg)
    run(game.game_callback(c, q))
    assert q.answers[-1] == (None, False) and 'You are X' in msg.edits[-1]
    q = FakeQuery('vga:77:XXXX-----:1', uid=77, message=msg)
    run(game.game_callback(c, q))
    assert q.answers[-1][1]
    q = FakeQuery('vga:77:XXXOO----:r', uid=77, message=msg)
    run(game.game_callback(c, q))
    assert 'You are X' in msg.edits[-1]
    plugin_manager._enabled['game'] = False
    q = FakeQuery('vga:77:---------:4', uid=77, message=msg)
    run(game.game_callback(c, q))
    assert q.answers[-1][1] and 'disabled' in q.answers[-1][0].lower()

def test_widget_gate_identity_serialization_and_secret_safe_failure(monkeypatch, caplog):
    from plugins.game import plugin as game
    class Outgoing:
        id = 123456
        calls = []
        async def __call__(self, method):
            self.calls.append(method)
            return SimpleNamespace(message_id=1)
    outgoing = Outgoing()
    monkeypatch.setattr(game.botapi, 'enabled', lambda: True)
    monkeypatch.setattr(game.botapi, 'bot', lambda: outgoing)
    client = FakeClient()
    assert not run(game.send_widget(client, -100123)) and not outgoing.calls
    monkeypatch.setenv('GAME_WIDGETS_BETA', 'true')
    client.me.id = 99
    assert not run(game.send_widget(client, -100123)) and not outgoing.calls
    client.me.id = 123456
    assert run(game.send_widget(client, -100123))
    method = outgoing.calls[-1]
    data = method.model_dump(exclude_none=True)
    assert method.__api_method__ == 'sendWidget' and data['chat_id'] == -100123
    assert data['html'].startswith('<!doctype html>')
    assert 'allow_queries' not in data and 'is_shared' not in data
    async def fail(_):
        raise RuntimeError('private-token-in-url')
    class Broken(Outgoing):
        __call__ = staticmethod(fail)
    monkeypatch.setattr(game.botapi, 'bot', lambda: Broken())
    assert not run(game.send_widget(client, 77))
    assert 'private-token-in-url' not in caplog.text

def test_plugin_discovery_and_widget_size():
    import zlib
    from core.plugin_manager import plugin_manager
    from plugins.game import plugin as game
    assert 'game' in plugin_manager.manifests
    assert not plugin_manager.command_conflicts(plugin_manager.manifests['game'])
    assert len(game.GAMES) == 21 and len(game.GAME_KEYS) == 21
    for key, _ in game.GAMES:
        raw = game.widget_html(key).encode()
        assert len(raw) <= 32768 and len(zlib.compress(raw, 9)) < 3800
        assert b'fetch(' not in raw and b'<input' not in raw and b'<form' not in raw
        assert b'<svg' in raw and b'VIDEL ARCADE' in raw
        assert b'<style>' in raw and b'<script>' in raw
        assert b'/* GAME_CODE */' not in raw and b'/* ARCADE_STYLES */' not in raw
        assert b'/* CLASSIC_ENGINE */' not in raw and b'<script src=' not in raw
    with pytest.raises(ValueError):
        game.widget_html('../../etc/passwd')


def test_menu_all_games_callbacks_and_beta_disabled(monkeypatch):
    from plugins.game import plugin as game
    c, msg = FakeClient(), FakeMsg('board')
    seen = set()
    for page in range(4):
        text, markup = game.game_menu(77, page)
        assert '23 single-player games' in text
        for row in markup.inline_keyboard:
            for button in row:
                assert len(button.callback_data.encode()) <= 64
                if ':play:' in button.callback_data:
                    seen.add(button.callback_data.split(':')[-1])
    assert seen == game.GAME_KEYS
    foreign = FakeQuery('vgg:77:page:0', uid=78, message=msg)
    run(game.game_menu_callback(c, foreign))
    assert foreign.answers[-1][1] and not msg.edits
    page = FakeQuery('vgg:77:page:1', uid=77, message=msg)
    run(game.game_menu_callback(c, page))
    assert 'Page 2/4' in msg.edits[-1]
    disabled = FakeQuery('vgg:77:play:snake', uid=77, message=msg)
    run(game.game_menu_callback(c, disabled))
    assert disabled.answers[-1][1] and 'unavailable' in disabled.answers[-1][0].lower()
    invalid = FakeQuery('vgg:77:play:unknown', uid=77, message=msg)
    run(game.game_menu_callback(c, invalid))
    assert invalid.answers[-1][1]


def test_menu_sends_selected_widget_in_original_topic(monkeypatch):
    from plugins.game import plugin as game
    c, msg = FakeClient(), FakeMsg('board', chat_id=-100123)
    msg.message_thread_id = 901
    q = FakeQuery('vgg:77:play:maze', uid=77, message=msg)
    monkeypatch.setenv('GAME_WIDGETS_BETA', '1')
    monkeypatch.setattr(game.botapi, 'enabled', lambda: True)
    calls = []
    async def send(client, chat, key, thread):
        calls.append((client, chat, key, thread))
        return True
    monkeypatch.setattr(game, 'send_widget', send)
    run(game.game_menu_callback(c, q))
    assert calls == [(c, -100123, 'maze', 901)]
    assert q.answers[-1][0] == 'Opening game…'


def test_template_assembly_rejects_broken_or_unsafe_sources(monkeypatch, tmp_path):
    from plugins.game import plugin as game
    with pytest.raises(ValueError):
        game._replace_once('missing', 'marker', 'replacement')
    with pytest.raises(ValueError):
        game._replace_once('marker marker', 'marker', 'replacement')
    widget = tmp_path / 'arcade.html'
    widget.write_text('<style>/* CLASSIC_STYLES */</style><script>/* CLASSIC_ENGINE */</script>')
    (tmp_path / 'classic.css').write_text('body{color:red}</style>')
    (tmp_path / 'classic.js').write_text('void 0;')
    monkeypatch.setattr(game, 'WIDGET', widget)
    with pytest.raises(ValueError):
        game.widget_html('classic')


def test_chat_game_menu_rounds_owner_and_restart(monkeypatch):
    from plugins.game import chat_games as cg
    text, menu = cg.act('vgc:77:menu', 77)
    assert 'without widget beta' in text
    assert sum(len(row) for row in menu.inline_keyboard) == 5
    for row in menu.inline_keyboard:
        for button in row:
            assert len(button.callback_data.encode()) <= 64
    with pytest.raises(cg.GameError, match='someone else'):
        cg.act('vgc:77:nim:17:r', 78)
    for data in ('vgc:77:nim:99:1', 'vgc:77:nim:1:3', 'vgc:77:rps:3:3:r',
                 'vgc:77:high:1:2:8:h', 'vgc:77:high:0:0:13:h', 'vgc:77:unknown'):
        with pytest.raises(cg.GameError):
            cg.act(data, 77)
    text, board = cg.act('vgc:77:nim:17:r', 77)
    assert 'Stones left: 17' in text
    text, board = cg.act(board.inline_keyboard[0][0].callback_data, 77)
    assert 'Stones left: 15' in text
    while 'Stones left: 0' not in text:
        choices = board.inline_keyboard[0]
        text, board = cg.act(choices[-1].callback_data, 77)
    assert 'You win' in text or 'Bot wins' in text
    monkeypatch.setattr(cg.secrets, 'randbelow', lambda n: 2)
    text, board = cg.act('vgc:77:rps:0:0:n', 77)
    for _ in range(3):
        text, board = cg.act(board.inline_keyboard[0][0].callback_data, 77)
    assert 'You win' in text and 'You 3 · Bot 0' in text
    assert 'New game' in board.inline_keyboard[0][0].text
    monkeypatch.setattr(cg.secrets, 'randbelow', lambda n: 0)
    text, board = cg.act('vgc:77:high:0:0:1:n', 77)
    for _ in range(8):
        text, board = cg.act(board.inline_keyboard[0][0].callback_data, 77)
    assert 'Round: 8/8' in text and 'Game over' in text
    assert len(board.inline_keyboard) == 1


def test_chat_game_callback_and_categories():
    from plugins.game import plugin as game
    from plugins.game import chat_games
    from core.plugin_manager import plugin_manager
    c, msg = FakeClient(), FakeMsg('board')
    q = FakeQuery('vgc:77:menu', uid=78, message=msg)
    run(game.chat_game_callback(c, q))
    assert q.answers[-1][1] and not msg.edits
    q = FakeQuery('vgc:77:menu', uid=77, message=msg)
    run(game.chat_game_callback(c, q))
    assert 'Chat games' in msg.edits[-1]
    plugin_manager._enabled['game'] = False
    q = FakeQuery('vgc:77:menu', uid=77, message=msg)
    run(game.chat_game_callback(c, q))
    assert q.answers[-1][1] and 'disabled' in q.answers[-1][0].lower()
    plugin_manager._enabled['game'] = True
    for category, _ in game.CATEGORIES:
        text, markup = game.game_menu(77, 0, category)
        assert '23 single-player games' in text
        for row in markup.inline_keyboard:
            for button in row:
                assert len(button.callback_data.encode()) <= 64
                if ':play:' in button.callback_data and category != 'all':
                    key = button.callback_data.split(':')[-1]
                    assert ('classic' if key == 'classic' else game.STYLES[key][0]) == category
    q = FakeQuery('vgg:77:cat:action0', uid=77, message=msg)
    run(game.game_menu_callback(c, q))
    assert 'Action' in msg.edits[-1]
    q = FakeQuery('vgg:77:cat:invalid0', uid=77, message=msg)
    run(game.game_menu_callback(c, q))
    assert q.answers[-1][1]
    for builder, args in ((chat_games.menu, (99999999999999999999,)),
                          (chat_games.nim_screen, (99999999999999999999, 17)),
                          (chat_games.rps_screen, (99999999999999999999, 2, 2)),
                          (chat_games.higher_screen, (99999999999999999999, 7, 7, 12))):
        _, markup = builder(*args)
        assert all(len(b.callback_data.encode()) <= 64 for row in markup.inline_keyboard for b in row)
