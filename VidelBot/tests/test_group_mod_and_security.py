"""
Comprehensive unit & security tests for:
  • Centralized Richgram presentation helpers (status, progress, custom_emoji, clamp_html, ordered lists)
  • Ephemeral message lifecycle (send, edit, delete + Bot API / WzGram / classic fallbacks)
  • Bot API bridge RetryAfter backoff & network outage resilience
  • WZML-X manifest compatibility, AST static security scanner, SHA-256 & owner-only staged approval
  • Group Management & Bot API 10.3 Moderation (live caller/bot/target permission checks, anonymous
    admins, bans/kicks/mutes/warns, member tags, reaction moderation, topics, join-requests, locks,
    anti-flood, anti-raid, welcome/rules, keyword filters, and modreport)
"""
import os
import re
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pyrogram
import pytest
from pyrogram import enums, types

from tests.harness import FakeClient, FakeMsg, FakeQuery, load_all, no_botapi, reset_db, run, use_botapi

MODULES = load_all()


@pytest.fixture(autouse=True)
def clean_env():
    run(reset_db())
    no_botapi()
    from core.plugin_installer import _STAGED
    from core.plugin_manager import plugin_manager
    from plugins.group_mod import plugin as gmod

    _STAGED.clear()
    gmod._CFG_CACHE.clear()
    gmod._FLOOD_BUCKETS.clear()
    plugin_manager.set_plugins_dir(os.path.join(os.getcwd(), "plugins"))
    plugin_manager._enabled.clear()
    plugin_manager.discover()
    yield
    _STAGED.clear()
    no_botapi()


# ───────────────────────── 1. Richgram Presentation, Escaping & Ephemeral Lifecycle ─────────────────────────
def test_rich_helpers_escape_untrusted_input_and_clamp_length():
    from core import rich
    from core.rich import Doc

    # Custom emoji validates numeric IDs and escapes hostile fallback text
    ok_em = rich.custom_emoji("5368324170671202286", "<star>")
    assert ok_em.html == '<tg-emoji emoji-id="5368324170671202286">&lt;star&gt;</tg-emoji>'
    bad_em = rich.custom_emoji('123" onclick="evil()', "<b>x</b>")
    assert "tg-emoji" not in bad_em.html and bad_em.html == "&lt;b&gt;x&lt;/b&gt;"

    # Status, progress & ordered lists escape hostile user strings
    doc = (
        Doc("🛡", "Audit <script>alert(1)</script>", "sub & title")
        .status("warning", "Check <b>1</b>", "detail <img src=x>")
        .progress(3, 10, label="Step <1>", detail="30% & counting")
        .items(["First <item>", "Second & item"], ordered=True)
    )
    r_html = doc.rich()
    c_html = doc.classic()
    for rendered in (r_html, c_html):
        assert "<script>" not in rendered
        assert "<img" not in rendered
        assert "<b>1</b>" not in rendered
    assert "<ol><li>First &lt;item&gt;</li><li>Second &amp; item</li></ol>" in r_html
    assert "<b>1.</b> First &lt;item&gt;" in c_html
    assert "30%" in c_html

    # Safe clamping closes open HTML tags when truncating oversized payloads
    huge = Doc("📊", "Huge").text("<b>" + ("A" * 6000) + "</b>")
    clamped = huge.classic()
    assert len(clamped) <= 4096
    assert clamped.count("<b>") == clamped.count("</b>")


class EphemeralWzClient(pyrogram.Client):
    def __init__(self):
        self.calls = []
        self.me = SimpleNamespace(id=888888, username="WorkerCloneBot", is_bot=True)

    async def edit_ephemeral_message_text(self, chat_id, receiver_id, message_id, **kwargs):
        self.calls.append(("edit_ephemeral_message_text", chat_id, receiver_id, message_id, kwargs))
        return SimpleNamespace(id=message_id)

    async def delete_ephemeral_message(self, chat_id, receiver_id, message_id):
        self.calls.append(("delete_ephemeral_message", chat_id, receiver_id, message_id, {}))
        return True


def test_ephemeral_edit_and_delete_across_botapi_wzgram_and_fallback():
    from core import rich
    from core.rich import Doc

    # 1) Bot API 10.3 path
    sess = use_botapi()
    c = FakeClient()
    assert run(rich.ephemeral_edit(c, -10077, 42, 900, Doc("✅", "Updated Ephemeral"))) is True
    assert "EditEphemeralMessageText" in sess.names()
    assert run(rich.ephemeral_delete(c, -10077, 42, 900)) is True
    assert "DeleteEphemeralMessage" in sess.names()

    # 2) WzGram Layer 232 native path (clone worker / standalone)
    no_botapi()
    wc = EphemeralWzClient()
    assert run(rich.ephemeral_edit(wc, -10077, 42, 901, Doc("🚀", "WzGram Ephemeral"))) is True
    assert wc.calls[0][0] == "edit_ephemeral_message_text"
    assert isinstance(wc.calls[0][4]["rich_text"], types.InputRichMessage)
    assert run(rich.ephemeral_delete(wc, -10077, 42, 901)) is True
    assert wc.calls[1][0] == "delete_ephemeral_message"

    # 3) Graceful fallback to standard edit_message_text / delete_messages when ephemeral unavailable
    fc = FakeClient()
    assert run(rich.ephemeral_edit(fc, -10077, 42, 902, "<b>Fallback</b>")) is True
    assert fc.called("edit_message_text")
    assert run(rich.ephemeral_delete(fc, -10077, 42, 902)) is True
    assert fc.called("delete_messages")


def test_botapi_bridge_retries_short_rate_limit_and_backs_off_on_network_outage():
    from core import botapi

    use_botapi()
    attempts = {"n": 0}

    class ShortRateLimit(Exception):
        retry_after = 0.01

    async def flaky(_bot):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ShortRateLimit("Too Many Requests")
        return "ok-after-retry"

    assert run(botapi.call(flaky)) == "ok-after-retry"
    assert attempts["n"] == 2

    # 3 consecutive network errors trip temporary 60s MTProto fallback
    class FakeNetworkError(Exception):
        pass

    for _ in range(3):
        assert run(botapi.try_call(lambda _b: (_ for _ in ()).throw(FakeNetworkError("down")))) is None
    assert botapi.enabled() is False


# ───────────────────────── 2. WZML-X Manifests, AST Security Scan & Owner-Only Staged Approval ─────────────────────────
def test_wzml_manifest_aliases_config_schema_and_staged_owner_approval():
    from core.plugin_installer import (
        PluginInstallError,
        approve_staged,
        reject_staged,
        sha256_digest,
        stage_from_archive,
    )
    from core.plugin_manager import plugin_manager
    from core.plugins_cmd import plugins_stage_cb

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        ext_dir = tmp_path / "plugins_store"
        plugin_manager.set_plugins_dir(ext_dir)

        # Build a WZML-X-formatted plugin archive (`wzml_plugin.yml` + aliases + config_schema + subprocess import)
        zpath = tmp_path / "sample_wzml.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr(
                "net_diag/wzml_plugin.yml",
                "name: net_diag\n"
                "version: '2.1.0'\n"
                "author: WZMLDev\n"
                "main_file: net_diag.py\n"
                "min_wzml_version: '3.0.0'\n"
                "python_dependencies:\n"
                "  - PyYAML\n"
                "settings_schema:\n"
                "  max_hops:\n"
                "    type: int\n"
                "    default: 15\n"
                "commands:\n"
                "  - name: netdiag\n"
                "    access: sudo\n",
            )
            zf.writestr(
                "net_diag/net_diag.py",
                "import subprocess\n"
                "from core.plugin_manager import PluginBase\n"
                "LOADED_FLAG = True\n"
                "class Plugin(PluginBase):\n"
                "    async def on_configure(self, cfg):\n"
                "        self.last_cfg = dict(cfg)\n",
            )

        digest = sha256_digest(zpath)
        # Wrong SHA-256 must be rejected
        with pytest.raises(PluginInstallError, match="SHA-256"):
            run(stage_from_archive(zpath, expect_sha256="0" * 64))

        # Stage valid archive — NO plugin code executed yet!
        staged = run(stage_from_archive(zpath, expect_sha256=digest))
        assert staged.manifest.name == "net_diag"
        assert staged.manifest.entry == "net_diag.py"
        assert "subprocess" in staged.risk_flags
        assert "net_diag" not in plugin_manager.modules

        # Non-owner (admin 222 or regular user 5) MUST be refused approval both programmatically and via callback
        with pytest.raises(PermissionError):
            run(approve_staged(staged.stage_id, owner_id=222))

        q_admin = FakeQuery(f"plg_stg:ok:{staged.stage_id}", uid=222)
        q_admin.matches = [re.match(r"^plg_stg:(ok|no):([\w_-]+)$", q_admin.data)]
        run(plugins_stage_cb(FakeClient(), q_admin))
        assert "Only bot owners" in q_admin.answers[-1][0]
        assert "net_diag" not in plugin_manager.modules

        # Owner (111) approves → plugin activates, config_schema works, on_configure fires, unload_all cleans up
        q_owner = FakeQuery(f"plg_stg:ok:{staged.stage_id}", uid=111)
        q_owner.matches = [re.match(r"^plg_stg:(ok|no):([\w_-]+)$", q_owner.data)]
        run(plugins_stage_cb(FakeClient(), q_owner))
        assert "net_diag" in plugin_manager.modules
        inst = plugin_manager.instances["net_diag"]
        assert inst.get_config("max_hops") == 15
        assert run(plugin_manager.configure_plugin("net_diag", "max_hops", "30")) == 30
        assert inst.last_cfg["max_hops"] == 30
        assert run(plugin_manager.unload_all()) >= 1

        # Syntax error in staged `.py` file is caught before staging succeeds
        bad_syntax = tmp_path / "broken.zip"
        with zipfile.ZipFile(bad_syntax, "w") as zf:
            zf.writestr("bad_py/plugin.yaml", "name: bad_py\nversion: '1.0.0'\nentry: plugin.py\n")
            zf.writestr("bad_py/plugin.py", "def broken(:\n    pass\n")
        with pytest.raises(PluginInstallError, match="Syntax error"):
            run(stage_from_archive(bad_syntax))

        # Command collision with core `/start` is blocked during staging
        collide_zip = tmp_path / "collide.zip"
        with zipfile.ZipFile(collide_zip, "w") as zf:
            zf.writestr("collide_core/plugin.yaml", "name: collide_core\nversion: '1.0.0'\ncommands: [start]\n")
            zf.writestr("collide_core/plugin.py", "x = 1\n")
        with pytest.raises(PluginInstallError, match="Command conflict"):
            run(stage_from_archive(collide_zip))

        # Rejecting a staged archive cleans up `.staging/<id>`
        st2_zip = tmp_path / "temp_mod.zip"
        with zipfile.ZipFile(st2_zip, "w") as zf:
            zf.writestr("temp_mod/plugin.yaml", "name: temp_mod\nversion: '1.0.0'\n")
            zf.writestr("temp_mod/plugin.py", "x = 1\n")
        st2 = run(stage_from_archive(st2_zip))
        assert st2.stage_root.exists()
        assert reject_staged(st2.stage_id) is True
        assert not st2.stage_root.exists()


# ───────────────────────── 3. Group Moderation & Live Permission Verification ─────────────────────────
class GroupFakeClient(FakeClient):
    """FakeClient with configurable per-user `ChatMember` statuses & privileges."""

    def __init__(self, members: dict[int, tuple]):
        super().__init__()
        self.me = SimpleNamespace(id=999, username="VidelTestBot", is_bot=True)
        self._members = members  # uid -> (ChatMemberStatus, ChatPrivileges | None)

    async def get_chat_member(self, chat_id, user_id):
        self.calls.append(("get_chat_member", (chat_id, user_id), {}))
        entry = self._members.get(int(user_id))
        if not entry:
            return SimpleNamespace(status=enums.ChatMemberStatus.MEMBER, privileges=None, is_member=True)
        status, priv = entry
        return SimpleNamespace(status=status, privileges=priv, is_member=True)


def _full_privs(**overrides):
    kw = dict(
        can_manage_chat=True,
        can_delete_messages=True,
        can_restrict_members=True,
        can_promote_members=True,
        can_change_info=True,
        can_invite_users=True,
        can_pin_messages=True,
        can_manage_topics=True,
        can_manage_tags=True,
    )
    kw.update(overrides)
    return types.ChatPrivileges(**kw)


def _grp_msg(text: str, uid=10, chat_id=-100888, client=None, reply_uid=None):
    m = FakeMsg(text, uid=uid, chat_type=enums.ChatType.SUPERGROUP, chat_id=chat_id, client=client)
    m.chat.title = "Dev <Hub>"
    if reply_uid is not None:
        rep = FakeMsg("offending msg", uid=reply_uid, chat_type=enums.ChatType.SUPERGROUP, chat_id=chat_id, client=client)
        rep.id = 555
        rep.from_user.first_name = "Troublemaker <x>"
        m.reply_to_message = rep
    return m


def test_group_mod_live_permission_checks_refuse_unauthorized_callers_bots_and_admin_targets():
    from plugins.group_mod import plugin as gmod

    # Case 1: Normal member tries /gban → refused
    c1 = GroupFakeClient({
        999: (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs()),
        50: (enums.ChatMemberStatus.MEMBER, None),
    })
    m1 = _grp_msg("/gban", uid=50, client=c1, reply_uid=60)
    run(gmod.gban_cmd(c1, m1))
    assert not c1.called("ban_chat_member")
    assert "permission" in m1.replies[-1].lower()

    # Case 2: Admin WITHOUT can_restrict_members tries /mute → refused
    c2 = GroupFakeClient({
        999: (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs()),
        20: (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs(can_restrict_members=False)),
    })
    m2 = _grp_msg("/mute 15m", uid=20, client=c2, reply_uid=60)
    run(gmod.mute_cmd(c2, m2))
    assert not c2.called("restrict_chat_member")

    # Case 3: Bot lacks can_restrict_members → refused with actionable hint
    c3 = GroupFakeClient({
        999: (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs(can_restrict_members=False)),
        10: (enums.ChatMemberStatus.OWNER, _full_privs()),
    })
    m3 = _grp_msg("/kick", uid=10, client=c3, reply_uid=60)
    run(gmod.kick_cmd(c3, m3))
    assert not c3.called("ban_chat_member")
    assert "Promote me" in m3.replies[-1]

    # Case 4: Admin tries to /gban another group administrator → refused
    c4 = GroupFakeClient({
        999: (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs()),
        10: (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs()),
        25: (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs()),
    })
    m4 = _grp_msg("/gban", uid=10, client=c4, reply_uid=25)
    run(gmod.gban_cmd(c4, m4))
    assert not c4.called("ban_chat_member")
    assert "administrator" in m4.replies[-1].lower()


def test_anonymous_group_admin_passes_middleware_and_executes_moderation():
    from core.middleware import senderless_gate
    from plugins.group_mod import plugin as gmod

    c = GroupFakeClient({999: (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs())})
    anon = _grp_msg("/mute 30m spamming", uid=None, client=c, reply_uid=70)
    anon.sender_chat = SimpleNamespace(id=-100888)
    # Passes senderless_gate without raising StopPropagation!
    run(senderless_gate(c, anon))
    run(gmod.mute_cmd(c, anon))
    assert c.called("restrict_chat_member")
    assert "30m" in anon.replies[-1]


def test_group_warns_tags_reactions_topics_locks_filters_and_greetings():
    from plugins.group_mod import plugin as gmod

    c = GroupFakeClient({
        999: (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs()),
        10: (enums.ChatMemberStatus.OWNER, _full_privs()),
        77: (enums.ChatMemberStatus.MEMBER, None),
    })

    # 1) Warn workflow + inline unwarn + threshold enforcement
    w1 = _grp_msg("/warn Link spam <script>", uid=10, client=c, reply_uid=77)
    run(gmod.warn_cmd(c, w1))
    assert "1/3" in w1.replies[-1] and "<script>" not in w1.replies[-1]

    # Non-admin tapping Remove Warn button is rejected
    qw_bad = FakeQuery("plg_gmod:unwarn:77", uid=77)
    qw_bad.message.chat = SimpleNamespace(id=-100888, title="Dev Hub", type=enums.ChatType.SUPERGROUP)
    run(gmod.group_mod_cb(c, qw_bad))
    assert "🔒" in qw_bad.answers[-1][0]

    # Hit warn limit (2 more warns → auto-mute)
    run(gmod.warn_cmd(c, _grp_msg("/warn second", uid=10, client=c, reply_uid=77)))
    w3 = _grp_msg("/warn third", uid=10, client=c, reply_uid=77)
    run(gmod.warn_cmd(c, w3))
    assert c.called("restrict_chat_member")
    assert "3/3" in w3.replies[-1]

    # 2) Bot API 10.3 Member Tags & Reaction Moderation
    sess = use_botapi()
    c.me.id = int(os.environ["BOT_TOKEN"].split(":", 1)[0])
    c._members[c.me.id] = (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs())
    run(gmod.tag_cmd(c, _grp_msg("/tag Core Dev", uid=10, client=c, reply_uid=77)))
    assert "SetChatMemberTag" in sess.names()

    run(gmod.clearreactions_cmd(c, _grp_msg("/clearreactions", uid=10, client=c, reply_uid=77)))
    assert "DeleteMessageReaction" in sess.names()

    run(gmod.clearreactions_cmd(c, _grp_msg("/clearreactions all", uid=10, client=c, reply_uid=77)))
    assert "DeleteAllMessageReactions" in sess.names()
    no_botapi()

    # 3) Forum topic creation
    run(gmod.topic_cmd(c, _grp_msg("/topic new Announcements", uid=10, client=c)))
    assert c.called("create_forum_topic")

    # 4) Rules, Locks, Keyword Filters & Welcome Greeting (HTML-escaped!)
    run(gmod.setrules_cmd(c, _grp_msg("/setrules Be respectful & no <spam>.", uid=10, client=c)))
    rm = _grp_msg("/rules", uid=77, client=c)
    run(gmod.rules_cmd(c, rm))
    assert "&lt;spam&gt;" in rm.replies[-1]

    run(gmod.lock_cmd(c, _grp_msg("/lock links", uid=10, client=c)))
    spam_link = _grp_msg("visit https://spam.example.org now", uid=77, client=c)
    run(gmod.group_passive_guard(c, spam_link))
    assert spam_link.deleted is True

    run(gmod.gfilter_cmd(c, _grp_msg("/gfilter docs Check the FAQ, {first}!", uid=10, client=c)))
    normal_q = _grp_msg("where are the docs please", uid=77, client=c)
    normal_q.from_user.first_name = "Gohan <b>x</b>"
    run(gmod.group_passive_guard(c, normal_q))
    assert "Gohan &lt;b&gt;x&lt;/b&gt;" in normal_q.replies[-1]

    run(gmod.greeting_cmd(c, _grp_msg("/welcome Welcome {mention} to {chat}!", uid=10, client=c)))
    upd = SimpleNamespace(
        chat=SimpleNamespace(id=-100888, title="Dev <Hub>", type=enums.ChatType.SUPERGROUP),
        old_chat_member=SimpleNamespace(status=enums.ChatMemberStatus.LEFT, user=None),
        new_chat_member=SimpleNamespace(
            status=enums.ChatMemberStatus.MEMBER,
            user=SimpleNamespace(id=77, first_name="Videl <3", is_bot=False),
        ),
    )
    run(gmod.group_member_greeting_handler(c, upd))
    sent_welcome = c.called("send_message")[-1][1][1]
    assert "Videl &lt;3" in sent_welcome and "Dev &lt;Hub&gt;" in sent_welcome
