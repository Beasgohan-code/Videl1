"""Phase 13 – FileStore-style mixed UI: small-caps labels + native Bot API rich messages
(headings, tables, details) on user-facing screens, with a classic HTML fallback."""
import re

import pytest

from tests.harness import (FakeClient, FakeMsg, FakeQuery, load_all, no_botapi, reset_db, run,
                           use_botapi)
from tests.test_phase5 import _bot_api_html_problems

MODULES = load_all()

# Rich HTML tags this bot emits – all documented for sendRichMessage (html mode)
RICH_TAGS = {"h1", "h3", "p", "i", "b", "code", "a", "table", "caption", "tr", "th", "td", "details",
             "summary", "ul", "ol", "li", "footer", "blockquote", "hr"}


@pytest.fixture(autouse=True)
def fresh():
    run(reset_db())
    no_botapi()
    yield
    no_botapi()


async def _all_docs():
    """Every rich screen builder → Doc (with some hostile values mixed in)."""
    from core.growth import refer_doc
    from core.menus import about_doc, help_doc, tools_doc
    from core.payments import _sub_doc
    from renamer.handlers import leaderboard_doc
    from saver.premium import plan_doc, premium_doc
    from saver.settings import commands_doc
    from VideoEncoder.plugins.start import stats_doc
    from VideoEncoder.plugins.status import status_doc
    c = FakeClient()
    return {
        "help": help_doc(), "about": await about_doc(c), "tools": tools_doc("Videl<Bot>"),
        "premium": premium_doc(), "plan": (await plan_doc(5, "<b>x</b>"))[0], "commands": commands_doc(),
        "refer": (await refer_doc(c, 5))[0], "leaderboard": (await leaderboard_doc("all", 5))[0],
        "stats": await stats_doc(None), "status": status_doc(),
        "sub_none": _sub_doc(None, False),
        "sub": _sub_doc({"stars": 90, "renewals": 2, "canceled": True}, "2026-12-01"),
    }


# ───────────────────────── style helpers ─────────────────────────
def test_small_caps_keeps_markup_and_technical_tokens():
    from core.style import sc
    out = sc('Send <b>Bold</b> /start to @VidelBot {title} <code>Keep Me</code> https://t.me/X &amp; CPU 4GB+')
    assert "ꜱᴇɴᴅ" in out and "<b>ʙᴏʟᴅ</b>" in out
    for token in ("/start", "@VidelBot", "{title}", "<code>Keep Me</code>", "https://t.me/X", "&amp;", "CPU", "4GB+"):
        assert token in out, token


def test_small_caps_and_headings_always_escape_bare_html_chars():
    from core.style import hdr, sans_bold, sc
    assert sc("Tom & Jerry < 3") == "ᴛᴏᴍ &amp; ᴊᴇʀʀʏ &lt; 3"
    assert "&amp;" in hdr("🤝", "Refer & earn") and "&𝗮𝗺𝗽;" not in hdr("🤝", "Refer & earn")
    assert sans_bold("A&B") == "𝗔&amp;𝗕"
    assert not _bot_api_html_problems(hdr("🤝", "Refer & earn", "a < b & c"))


# ───────────────────────── Doc renderers ─────────────────────────
def test_doc_renders_native_rich_html_and_classic_filestore_html():
    from core.rich import Doc, Raw
    doc = (Doc("📊", "My plan", "free tier").table([("Plan", "<Free>"), ("Link", Raw("<b>ok</b>"))], header=("Item", "Value"))
           .h("🎛", "Streams").table([(1, "Video", "h264")], header=("#", "Type", "Codec"), align=("center", "left", "right"))
           .details("More", Doc().items(["a & b"])).footer("Updated now"))
    r = doc.rich()
    assert r.startswith("<h1>📊 My plan</h1>") and "<table bordered striped>" in r
    assert "<th>ɪᴛᴇᴍ</th>" in r and "<td>&lt;Free&gt;</td>" in r and "<td><b>ok</b></td>" in r
    assert '<td align="center">1</td>' in r and '<td align="right">h264</td>' in r
    assert "<details><summary>More</summary><ul><li>a &amp; b</li></ul></details>" in r
    assert "<footer>Updated now</footer>" in r and "<h3>🎛 ꜱᴛʀᴇᴀᴍꜱ</h3>" in r
    c = doc.classic()
    assert "𝗠𝗬 𝗣𝗟𝗔𝗡" in c and "◈ <b>ᴘʟᴀɴ:</b> &lt;Free&gt;" in c
    assert "◈ <b>1</b> · Video · h264" in c and "<blockquote expandable><b>More</b>" in c
    assert not _bot_api_html_problems(c)


def test_every_screen_is_valid_in_both_renderings():
    docs = run(_all_docs())
    for name, doc in docs.items():
        classic, rich_html = doc.classic(), doc.rich()
        assert not _bot_api_html_problems(classic), (name, _bot_api_html_problems(classic))
        assert len(classic) <= 4096, name
        tags = set(re.findall(r"</?([a-z0-9]+)", rich_html))
        assert tags <= RICH_TAGS, (name, tags - RICH_TAGS)
        assert rich_html.count("<table") == rich_html.count("</table>"), name
        assert "<b>x</b>" not in rich_html and "Videl<Bot>" not in rich_html, name   # user values escaped


def test_commands_screen_lists_every_user_command_once():
    from core.commands import USER_COMMANDS, user_sections
    from saver.settings import commands_doc
    flat = [c for _, _, cmds in user_sections() for c, _ in cmds]
    assert flat == [c for c, _ in USER_COMMANDS]
    r = commands_doc().rich()
    assert all(f"<td>/{c}</td>" in r for c, _ in USER_COMMANDS)


# ───────────────────────── keyboards on rich messages ─────────────────────────
def test_markup_ok_only_allows_rich_safe_callbacks():
    from pyrogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup as KB
    from core.menus import back_home_kb, help_kb
    from core.payments import _sub_kb
    from core.rich import markup_ok
    from saver.start import premium_markup
    assert markup_ok(None) and markup_ok(help_kb(222)) and markup_ok(help_kb(5, close=True))
    assert markup_ok(back_home_kb()) and markup_ok(premium_markup("myplan_back_btn"))
    assert markup_ok(_sub_kb(None)) and markup_ok(_sub_kb({"canceled": True}))
    assert markup_ok(KB([[Btn("Site", url="https://example.com")]]))
    assert not markup_ok(KB([[Btn("Encoder", callback_data="OpenSettings")]]))
    assert not markup_ok(KB([[Btn("x", callback_data="stars_buy:abc")]]))


def test_safe_callbacks_all_have_bot_api_safe_handlers():
    """Every sample callback allowed on a rich message is handled by a registered callback handler."""
    import inspect
    from pyrogram.handlers import CallbackQueryHandler
    from core.rich import SAFE_CALLBACKS
    samples = ["close_btn", "start_btn", "help_btn", "about_btn", "settings_btn", "help_enc", "help_tools",
               "help_admin", "clone_help", "help_rename", "cmd_list_btn", "premium_plans_btn", "myplan_back_btn",
               "settings_back_btn", "rn:home", "rnlb:week", "status ref", "refer_btn", "noop:plan",
               "stars_buy:30", "stars_sub", "sub_toggle:cancel"]
    handlers = {}
    for mod in MODULES:
        for obj in vars(mod).values():
            hs = getattr(obj, "handlers", None) if callable(obj) else None
            for h, group in (hs if isinstance(hs, list) else []):
                if isinstance(h, CallbackQueryHandler) and group >= 0:   # <0 = middleware gates
                    handlers[id(h)] = h
    for data in samples:
        assert SAFE_CALLBACKS.match(data), data
        hit = False
        for h in handlers.values():
            try:
                r = h.filters(None, FakeQuery(data, uid=222)) if h.filters else True
                hit = run(r) if inspect.isawaitable(r) else bool(r)
            except Exception:
                hit = False
            if hit:
                break
        assert hit, f"no handler for {data}"


# ───────────────────────── sending ─────────────────────────
def test_help_goes_out_as_a_native_rich_message():
    from core.menus import help_cmd
    s = use_botapi()
    c = FakeClient()
    m = FakeMsg("/help", uid=5, client=c)
    run(help_cmd(c, m))
    p = s.last("SendRichMessage")
    assert p and "<h1>📚 Help &amp; user guide</h1>" in p["rich_message"]["html"]
    assert "<table bordered striped>" in p["rich_message"]["html"]
    assert p["reply_markup"]["inline_keyboard"] and not m.replies
    assert "skip_entity_detection" not in p["rich_message"]      # /commands stay tappable


def test_classic_fallback_without_bot_api():
    from core.menus import help_cmd
    c = FakeClient()
    m = FakeMsg("/help", uid=5, client=c)
    run(help_cmd(c, m))
    assert "𝗛𝗘𝗟𝗣 &amp; 𝗨𝗦𝗘𝗥 𝗚𝗨𝗜𝗗𝗘" in m.replies[-1] and "◈" in m.replies[-1]


def test_unsafe_keyboard_forces_classic_even_with_bot_api():
    from pyrogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup as KB
    from core import rich
    from core.rich import Doc
    s = use_botapi()
    c = FakeClient()
    m = FakeMsg("/x", uid=5, client=c)
    run(rich.reply(m, Doc("🧪", "Test").text("hi"), KB([[Btn("Enc", callback_data="OpenSettings")]])))
    assert "SendRichMessage" not in s.names() and "𝗧𝗘𝗦𝗧" in m.replies[-1]


def test_menu_button_edits_into_a_rich_screen_and_marks_it():
    from core import rich
    from core.menus import menu_callbacks
    s = use_botapi()
    c = FakeClient()
    q = FakeQuery("about_btn")
    q.message._client = c
    run(menu_callbacks(c, q))
    p = s.last("EditMessageText")
    assert p and "<h1>ℹ️ About Videl</h1>" in p["rich_message"]["html"]
    assert "parse_mode" not in p and "text" not in p                  # text-only fields dropped
    assert rich.is_rich(q.message.chat.id, q.message.id)


def test_rich_screen_is_replaced_when_a_text_edit_is_refused():
    from core import rich
    from core.ui import smart_edit
    no_botapi()
    m = FakeMsg(text="menu", uid=0, client=FakeClient())
    rich.mark(m.chat.id, m.id)
    run(smart_edit(m, "<b>Home</b>"))
    assert m.replies[-1] == "<b>Home</b>" and not rich.is_rich(m.chat.id, m.id)


def test_groups_get_the_classic_rendering():
    from core.tools import id_cmd
    from pyrogram import enums
    s = use_botapi()
    c = FakeClient()
    m = FakeMsg("/id", uid=5, chat_type=enums.ChatType.SUPERGROUP, chat_id=-100, client=c)
    run(id_cmd(c, m))
    assert "SendRichMessage" not in s.names()
    sent = [str(p.get("text", "")) for n, p in s.payloads if n == "SendMessage"] + [str(x) for x in m.replies]
    assert any("𝗜𝗗𝗦" in t and "ᴄʜᴀᴛ ID" in t for t in sent), (s.names(), m.replies)


def test_mysub_and_leaderboard_render_rich_in_private():
    from core.payments import mysub_cmd
    from renamer.handlers import leaderboard_cmd
    s = use_botapi()
    c = FakeClient()
    run(mysub_cmd(c, FakeMsg("/mysub", uid=5, client=c)))
    run(leaderboard_cmd(c, FakeMsg("/leaderboard", uid=5, client=c)))
    htmls = [p["rich_message"]["html"] for n, p in s.payloads if n == "SendRichMessage"]
    assert any("My subscription" in h for h in htmls) and any("top renamers" in h for h in htmls)
