"""Opt-in icons preserve actions and retry only confirmed emoji rejections."""
from pyrogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup
from tests import harness  # initialize fake credentials and Mongo before importing core
from core import botapi, premium_icons


def keyboard():
    return InlineKeyboardMarkup([
        [Button('✅ Confirm', callback_data='yes'), Button('🔗 Site', url='https://example.com')],
        [Button('📁 Other', callback_data='other', icon_custom_emoji_id='12345'),
         Button('❌ Chip', callback_data='noop:1')],
    ])


def enable(monkeypatch):
    monkeypatch.setattr(premium_icons.config, 'PREMIUM_BUTTON_ICONS', True)
    monkeypatch.setattr(premium_icons.config, 'PREMIUM_BUTTON_ICON_BOT_IDS', [42])
    monkeypatch.setattr(botapi, 'BOT_TOKEN', '42:fake')


def test_default_off_and_per_bot_allowlist(monkeypatch):
    kb = keyboard()
    monkeypatch.setattr(premium_icons.config, 'PREMIUM_BUTTON_ICONS', False)
    monkeypatch.setattr(premium_icons.config, 'PREMIUM_BUTTON_ICON_BOT_IDS', [42])
    monkeypatch.setattr(botapi, 'BOT_TOKEN', '42:fake')
    assert botapi.convert_markup(kb, premium_icons=True).inline_keyboard[0][0].icon_custom_emoji_id is None
    enable(monkeypatch)
    for bot_id in (None, 43, '42', True, 0):
        assert not premium_icons.allowed_bot(bot_id)
    assert botapi.convert_markup(kb).inline_keyboard[0][0].icon_custom_emoji_id is None
    result = botapi.convert_markup(kb, premium_icons=True)
    first, second = result.inline_keyboard[0]
    assert (first.text, first.callback_data, first.icon_custom_emoji_id) == ('✅ Confirm', 'yes', '5084979757905347540')
    assert (second.text, second.url, second.icon_custom_emoji_id) == ('🔗 Site', 'https://example.com', '5271604874419647061')
    assert result.inline_keyboard[1][0].icon_custom_emoji_id == '12345'
    assert result.inline_keyboard[1][1].icon_custom_emoji_id is None
    assert kb.inline_keyboard[0][0].icon_custom_emoji_id is None
    monkeypatch.setattr(botapi, 'BOT_TOKEN', '43:fake')
    assert botapi.convert_markup(kb, premium_icons=True).inline_keyboard[0][0].icon_custom_emoji_id is None


def test_icon_only_for_simple_actions(monkeypatch):
    from core.ui import copy_button
    enable(monkeypatch)
    assert premium_icons.icon_for(copy_button('📄 Copy', 'secret'), 42) is None
    assert premium_icons.icon_for(Button('✅ X', callback_data='noop:1'), 42) is None
    assert premium_icons.icon_for(Button('Unknown', callback_data='ok'), 42) is None


def test_retry_only_confirmed_emoji_bad_request(monkeypatch):
    from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
    enable(monkeypatch)
    calls = []
    class FakeBot:
        async def send_message(self, **kw):
            calls.append(kw)
            if len(calls) == 1:
                raise TelegramBadRequest(method=None, message='BUTTON_ICON_INVALID')
            return True
    async def call(factory):
        return await factory(FakeBot())
    monkeypatch.setattr(botapi, 'colored', lambda: True)
    monkeypatch.setattr(botapi, 'call', call)
    assert harness.run(botapi.send_text(5, 'hi', keyboard()))
    assert len(calls) == 2
    assert calls[0]['reply_markup'].inline_keyboard[0][0].icon_custom_emoji_id
    assert calls[1]['reply_markup'].inline_keyboard[0][0].icon_custom_emoji_id is None
    assert calls[1]['reply_markup'].inline_keyboard[1][0].icon_custom_emoji_id == '12345'
    assert not botapi._premium_icon_rejected(TelegramNetworkError(method=None, message='emoji invalid'))
    assert not botapi._premium_icon_rejected(TelegramBadRequest(method=None, message='chat not found'))


def test_edit_retry_and_no_network_retry(monkeypatch):
    from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
    enable(monkeypatch)
    calls = []
    class FakeBot:
        async def edit_message_text(self, **kw):
            calls.append(kw)
            if len(calls) == 1:
                raise TelegramBadRequest(method=None, message='custom emoji invalid')
            return True
        async def send_message(self, **kw):
            calls.append(kw)
            raise TelegramNetworkError(method=None, message='connection lost')
    async def call(factory):
        return await factory(FakeBot())
    monkeypatch.setattr(botapi, 'colored', lambda: True)
    monkeypatch.setattr(botapi, 'call', call)
    assert harness.run(botapi.edit_text(5, 8, 'hi', keyboard()))
    assert len(calls) == 2 and calls[0]['reply_markup'].inline_keyboard[0][0].icon_custom_emoji_id
    assert calls[1]['reply_markup'].inline_keyboard[0][0].icon_custom_emoji_id is None
    calls.clear()
    assert not harness.run(botapi.send_text(5, 'hi', keyboard()))
    assert len(calls) == 1
