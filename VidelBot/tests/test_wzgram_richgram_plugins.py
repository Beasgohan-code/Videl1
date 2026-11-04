"""
Comprehensive tests for the WzGram v3.1.6 (Layer 232) + Richgram v0.2.0 +
Bot API 10.3 + WZML-X Modular Plugin Architecture upgrade.
"""
import io
import json
import os
import tarfile
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
def fresh_state():
    run(reset_db())
    no_botapi()
    from core.plugin_manager import plugin_manager
    plugin_manager.set_plugins_dir(os.path.join(os.getcwd(), "plugins"))
    plugin_manager._enabled.clear()
    plugin_manager.discover()
    yield
    no_botapi()


# ───────────────────────── 1. Runtime Stack & WzGram Layer 232 ─────────────────────────
def test_runtime_stack_verifies_wzgram_and_richgram():
    from client import runtime_stack
    st = runtime_stack()
    assert st["is_wzgram"] is True
    assert st["engine"] == "WzGram"
    assert st["layer"] >= 232
    assert st["rich_available"] is True
    assert st["native_rich"] is True
    assert st["warpcrypto"] is not None
    assert st["richgram"] is not None


def test_wzgram_idioms_link_preview_reply_params_and_styled_buttons():
    from core import botapi
    from core.ui import (
        copy_button,
        link_preview_options,
        reply_parameters,
        styled_button,
        wzgram_markup,
    )

    lpo = link_preview_options("https://example.com/poster.jpg", prefer_large_media=True, show_above_text=True)
    assert isinstance(lpo, types.LinkPreviewOptions)
    assert lpo.url == "https://example.com/poster.jpg"
    assert lpo.prefer_large_media is True and lpo.show_above_text is True
    assert link_preview_options().is_disabled is True

    rp = reply_parameters(42, chat_id=-1001, quote="hello")
    assert isinstance(rp, types.ReplyParameters)
    assert rp.message_id == 42 and rp.chat_id == -1001 and rp.quote == "hello"
    assert reply_parameters(None) is None

    cp = copy_button("📋 Copy", "secret-code", style="primary")
    assert isinstance(cp.copy_text, types.CopyTextButton)
    assert cp.copy_text.text == "secret-code"
    assert cp.style == enums.ButtonStyle.PRIMARY

    dis = styled_button("Locked", disabled=True, callback_data="ignored")
    assert isinstance(dis.disabled, types.DisabledButton)
    assert dis.callback_data is None

    kb = types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("💎 Buy", callback_data="stars_buy:30"),
         types.InlineKeyboardButton("❌ Delete", callback_data="do_delete_1")],
        [types.InlineKeyboardButton("Chip", callback_data="noop:1"),
         styled_button("Custom", callback_data="close_btn", style="primary")],
    ])
    decorated = wzgram_markup(kb)
    r0 = decorated.inline_keyboard[0]
    r1 = decorated.inline_keyboard[1]
    assert r0[0].style == enums.ButtonStyle.SUCCESS
    assert r0[1].style == enums.ButtonStyle.DANGER
    assert isinstance(r1[0].disabled, types.DisabledButton)
    assert r1[1].style == enums.ButtonStyle.PRIMARY

    # Also verify aiogram conversion preserves explicit WzGram ButtonStyle & DisabledButton
    aio_kb = botapi.convert_markup(decorated)
    assert aio_kb.inline_keyboard[0][0].style == "success"
    assert aio_kb.inline_keyboard[1][0].disabled is not None
    assert aio_kb.inline_keyboard[1][1].style == "primary"


class DummyWzClient(pyrogram.Client):
    """Subclass of pyrogram.Client that records WzGram native MTProto calls offline."""

    def __init__(self):
        # Do not start network sessions; only record outgoing calls
        self.recorded = []
        self.me = SimpleNamespace(id=999999, username="CloneWorkerBot", is_bot=True)

    async def send_message(self, chat_id, text="", **kwargs):
        self.recorded.append(("send_message", chat_id, text, kwargs))
        return SimpleNamespace(id=777, chat=SimpleNamespace(id=chat_id), text=text)

    async def edit_message_text(self, chat_id, message_id, text=None, **kwargs):
        self.recorded.append(("edit_message_text", chat_id, message_id, text, kwargs))
        return SimpleNamespace(id=message_id, chat=SimpleNamespace(id=chat_id), text=text)

    async def send_rich_message(self, chat_id, rich_text, **kwargs):
        self.recorded.append(("send_rich_message", chat_id, rich_text, kwargs))
        return SimpleNamespace(id=888, chat=SimpleNamespace(id=chat_id))

    async def send_rich_message_draft(self, chat_id, draft_id, **kwargs):
        self.recorded.append(("send_rich_message_draft", chat_id, draft_id, kwargs))
        return True


def test_native_wzgram_mtproto_rich_and_preview_transport():
    from core import rich
    from core.rich import Doc
    from core.ui import group_reply, send_with_preview

    wc = DummyWzClient()
    # 1) send_with_preview on WzGram uses LinkPreviewOptions + ReplyParameters + ButtonStyle
    kb = types.InlineKeyboardMarkup([[types.InlineKeyboardButton("💎 Upgrade", callback_data="buy_premium")]])
    run(send_with_preview(wc, 55, "<b>Hello</b>", reply_markup=kb, pic="https://example.com/a.jpg",
                          reply_to=12, quote="quoted"))
    kind, cid, txt, kw = wc.recorded[-1]
    assert kind == "send_message" and cid == 55
    assert isinstance(kw["link_preview_options"], types.LinkPreviewOptions)
    assert kw["link_preview_options"].url == "https://example.com/a.jpg"
    assert isinstance(kw["reply_parameters"], types.ReplyParameters)
    assert kw["reply_parameters"].quote == "quoted"
    assert kw["reply_markup"].inline_keyboard[0][0].style == enums.ButtonStyle.SUCCESS

    # 2) rich.send on WzGram uses native send_rich_message(InputRichMessage) even when aiogram is off!
    doc = (Doc("🚀", "WzGram Native", "layer 232")
           .kv_table([("Engine", "WzGram"), ("Crypto", "WarpCrypto")])
           .pull_quote("Fast pooled MTProto")
           .pre("print('ok')", language="python")
           .math("a^2 + b^2 = c^2")
           .buttons_row(rich.rich_button("Docs", url="https://t.me"))
           .slideshow(["https://example.com/1.jpg", "https://example.com/2.jpg"], caption="Slides"))
    r_html = doc.rich()
    assert "<aside>Fast pooled MTProto</aside>" in r_html
    assert "<tg-math-block>a^2 + b^2 = c^2</tg-math-block>" in r_html
    assert "<tg-button-row>" in r_html and "<tg-slideshow>" in r_html
    assert "WzGram Native" in doc.plain()
    c_html = doc.classic()
    assert "𝗪𝗭𝗚𝗥𝗔𝗠 𝗡𝗔𝗧𝗜𝗩𝗘" in c_html and "Media 1" in c_html

    safe_kb = types.InlineKeyboardMarkup([[types.InlineKeyboardButton("🏠 Home", callback_data="start_btn")]])
    mid = run(rich.send(wc, 55, doc, reply_markup=safe_kb, reply_to=10))
    assert mid == 888
    call_name, r_cid, irm, r_kw = wc.recorded[-1]
    assert call_name == "send_rich_message" and r_cid == 55
    assert isinstance(irm, types.InputRichMessage) and "<table bordered striped>" in irm.html

    # 3) group_reply on WzGram uses EphemeralMessageParameters natively
    grp_msg = FakeMsg("/id", uid=55, chat_type=enums.ChatType.SUPERGROUP, chat_id=-100500, client=wc)
    assert run(group_reply(grp_msg, "<b>Private to you</b>")) is True
    _, g_cid, _, g_kw = wc.recorded[-1]
    assert g_cid == -100500
    assert isinstance(g_kw["ephemeral_message_parameters"], types.EphemeralMessageParameters)
    assert g_kw["ephemeral_message_parameters"].receiver_user_id == 55


# ───────────────────────── 2. Stoppable Draft Streaming & ManagedBot Push ─────────────────────────
def test_stoppable_rich_draft_and_generation_stopped_handler():
    from core import stream

    wc = DummyWzClient()
    m = FakeMsg("/imdb Interstellar", uid=42, client=wc)

    async def _exercise():
        async with stream.progress(m, "Working…", can_stop=True) as prog:
            await prog.step("Stage 1")
            assert not prog.stopped
            # Simulate Telegram sending MessageGenerationStopped for this chat & draft_id
            evt = types.MessageGenerationStopped(
                client=wc,
                chat=types.Chat(id=42, type=enums.ChatType.PRIVATE),
                draft_id=prog.draft_id,
            )
            await stream.on_generation_stopped(wc, evt)
            assert prog.stopped is True
            # Subsequent draft updates become no-ops once stopped
            before = len(wc.recorded)
            await prog.step("Stage 2 after stop")
            assert len(wc.recorded) == before

    run(_exercise())
    assert any(c[0] == "send_rich_message_draft" and c[3].get("can_stop") is True for c in wc.recorded)


def test_managed_bot_updated_push_hook_connects_without_polling(monkeypatch):
    from filestore.main_bot.plugins import create_bot, managed_bots

    use_botapi(responses={"GetManagedBotToken": "777888:AAInstantManagedBotToken123456"})
    monkeypatch.setattr(
        create_bot,
        "validate_bot_token",
        lambda tok: __import__("asyncio").sleep(0, result={"id": 777888, "first_name": "Clone", "username": "instant_clone_bot"}),
    )
    status = FakeMsg("waiting", uid=0, chat_id=5)
    create_bot._creation_state[5] = {
        "step": "awaiting_token",
        "data": {"managed_username": "tester_123456_bot", "managed_status_msg": status},
    }
    upd = types.ManagedBotUpdated(
        user=types.User(id=5, first_name="Tester"),
        bot=types.User(id=777888, first_name="Clone", username="instant_clone_bot", is_bot=True),
    )
    try:
        run(managed_bots.on_managed_bot_updated(FakeClient(), upd))
        assert create_bot._creation_state[5]["step"] == "awaiting_channel"
        assert create_bot._creation_state[5]["data"]["managed"] is True
        assert any("Token verified" in e for e in status.edits)
    finally:
        create_bot._creation_state.pop(5, None)


# ───────────────────────── 3. Interactive /checklist & /nftgift ─────────────────────────
def test_interactive_checklist_and_business_native_checklist():
    from core.extras import checklist_cb, checklist_cmd, parse_checklist_input

    title, items = parse_checklist_input("/checklist Encode Batch :: Ep 01 | Ep 02 | Ep 03")
    assert title == "Encode Batch" and items == ["Ep 01", "Ep 02", "Ep 03"]

    c = FakeClient()
    m = FakeMsg("/checklist Encode Batch :: Ep 01 | Ep 02", uid=5, client=c)
    run(checklist_cmd(c, m))
    assert "0/2" in m.replies[-1]

    # Extract checklist ID from DB and toggle task 0, then complete all, then delete
    from core.db import vdb
    saved = run(vdb.db["checklists"].find_one({"owner": 5}))
    cid = saved["_id"]

    q_tog = FakeQuery(f"chk_tog:{cid}:0", uid=5)
    q_tog.matches = [__import__("re").match(r"^chk_(tog|all|del):([\w-]+)(?::(\d+))?$", q_tog.data)]
    run(checklist_cb(c, q_tog))
    assert any("1/2" in str(e) for e in q_tog.message.edits)

    q_all = FakeQuery(f"chk_all:{cid}", uid=5)
    q_all.matches = [__import__("re").match(r"^chk_(tog|all|del):([\w-]+)(?::(\d+))?$", q_all.data)]
    run(checklist_cb(c, q_all))
    assert any("2/2" in str(e) for e in q_all.message.edits)

    q_del = FakeQuery(f"chk_del:{cid}", uid=5)
    q_del.matches = [__import__("re").match(r"^chk_(tog|all|del):([\w-]+)(?::(\d+))?$", q_del.data)]
    run(checklist_cb(c, q_del))
    assert q_del.message.deleted is True

    # Business connection uses Bot API SendChecklist
    s = use_botapi()
    bm = FakeMsg("/checklist Ship :: Build | Deploy", uid=5, client=c)
    bm.business_connection_id = "biz_conn_99"
    run(checklist_cmd(c, bm))
    assert "SendChecklist" in s.names()


def test_nftgift_collectible_inspector():
    from core.extras import nftgift_cmd

    c = FakeClient()

    async def fake_upgraded_gift(link):
        return SimpleNamespace(
            title="Plush Pepe",
            number=7,
            total_upgraded_count=1000,
            model=SimpleNamespace(name="Gold Frog", rarity_permille=15),
            backdrop=SimpleNamespace(name="Cyberpunk", rarity_permille=20),
            symbol=SimpleNamespace(name="Crown", rarity_permille=5),
            owner=SimpleNamespace(first_name="Satoshi"),
            value_amount=250,
            value_currency="TON",
        )

    c.get_upgraded_gift = fake_upgraded_gift
    m = FakeMsg("/nftgift https://t.me/nft/PlushPepe-7", uid=5, client=c)
    run(nftgift_cmd(c, m))
    out = m.replies[-1]
    assert "𝗣𝗟𝗨𝗦𝗛 𝗣𝗘𝗣𝗘 #𝟳" in out and "Gold Frog (1.5%)" in out and "250 TON" in out


# ───────────────────────── 4. Plugin Manager & Safe Archive Installer ─────────────────────────
def test_plugin_manager_discovery_toggle_and_persistence():
    from core.plugin_manager import PluginManifest, plugin_manager
    from core.plugins_cmd import plugins_cmd, plugins_mgr_cb

    discovered = plugin_manager.discover()
    assert {"imdb", "anilist", "mydramalist", "exportsession"} <= set(discovered)

    # Toggle off → persisted in MongoDB → survives fresh sync_db()
    run(plugin_manager.disable("imdb"))
    assert plugin_manager.is_enabled("imdb") is False
    plugin_manager._enabled.clear()
    run(plugin_manager.sync_db())
    assert plugin_manager.is_enabled("imdb") is False

    # Re-enable via /plugins callback
    c = FakeClient()
    m = FakeMsg("/plugins", uid=111, client=c)
    run(plugins_cmd(c, m))
    assert "imdb" in m.replies[-1]

    q = FakeQuery("plg_mgr:tog:imdb", uid=111)
    q.matches = [__import__("re").match(r"^plg_mgr:(list|(info|tog|rel):([\w_-]+))$", q.data)]
    run(plugins_mgr_cb(c, q))
    assert plugin_manager.is_enabled("imdb") is True

    # Semver validation check
    with pytest.raises(ValueError):
        PluginManifest.from_dict({"name": "future_mod", "min_bot_version": "99.0.0"})


def test_plugin_installer_safe_zip_tar_and_traversal_blocking():
    from core.plugin_installer import (
        PluginInstallError,
        extract_safe_archive,
        install_from_archive,
        resolve_github_source,
        uninstall_plugin,
    )
    from core.plugin_manager import plugin_manager

    url, sub = resolve_github_source("gh:SilentDemonSD/WZML-X@wzv3-dev:plugins/imdb")
    assert url == "https://codeload.github.com/SilentDemonSD/WZML-X/tar.gz/wzv3-dev"
    assert sub == "plugins/imdb"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        ext_plugins_dir = tmp_path / "plugins_dir"
        plugin_manager.set_plugins_dir(ext_plugins_dir)

        # 1) Malicious zip with ../ traversal MUST be rejected
        bad_zip = tmp_path / "evil.zip"
        with zipfile.ZipFile(bad_zip, "w") as zf:
            zf.writestr("../escape.py", "x = 1")
        with pytest.raises(PluginInstallError, match="traversal"):
            extract_safe_archive(bad_zip, tmp_path / "out1")

        # 2) Malicious tar with symlink MUST be rejected
        bad_tar = tmp_path / "evil.tar.gz"
        with tarfile.open(bad_tar, "w:gz") as tf:
            ti = tarfile.TarInfo("demo/link.py")
            ti.type = tarfile.SYMTYPE
            ti.linkname = "/etc/passwd"
            tf.addfile(ti)
        with pytest.raises(PluginInstallError, match="symlink"):
            extract_safe_archive(bad_tar, tmp_path / "out2")

        # 3) Valid plugin archive installs, loads PluginBase, stores settings, and uninstalls cleanly
        good_zip = tmp_path / "hello_plugin.zip"
        with zipfile.ZipFile(good_zip, "w") as zf:
            zf.writestr(
                "hello_world/plugin.yaml",
                "name: hello_world\nversion: 1.0.1\nauthor: Tester\ndescription: Demo\ncommands:\n  - hello\n",
            )
            zf.writestr(
                "hello_world/plugin.py",
                "from core.plugin_manager import PluginBase\n"
                "class Plugin(PluginBase):\n"
                "    async def on_load(self, client=None):\n"
                "        await self.set_setting('greeted', True)\n",
            )
        manifest = run(install_from_archive(good_zip))
        assert manifest.name == "hello_world" and manifest.version == "1.0.1"
        inst = plugin_manager.instances["hello_world"]
        assert run(inst.get_setting("greeted")) is True
        assert run(uninstall_plugin("hello_world")) is True
        assert "hello_world" not in plugin_manager.manifests


# ───────────────────────── 5. Bundled Rich Plugins (/imdb, /anime, /mdl, /exportsession) ─────────────────────────
def test_imdb_plugin_search_and_detail_card(monkeypatch):
    from plugins.imdb import plugin as imdb_plg

    monkeypatch.setattr(
        imdb_plg,
        "search_imdb_sync",
        lambda q: [
            {"id": "tt0816692", "title": "Interstellar", "year": 2014, "kind": "movie", "rating": 8.7},
            {"id": "tt0133093", "title": "The Matrix", "year": 1999, "kind": "movie", "rating": 8.7},
        ],
    )
    monkeypatch.setattr(
        imdb_plg,
        "fetch_imdb_detail_sync",
        lambda tid: SimpleNamespace(
            imdb_id=tid,
            title="Interstellar",
            year=2014,
            year_end=None,
            kind="movie",
            cover_url="https://example.com/interstellar.jpg",
            rating=8.7,
            votes=2100000,
            metacritic_rating=74,
            duration=169,
            certificate="PG-13",
            genres=["Adventure", "Drama", "Sci-Fi"],
            country_codes=["US", "GB"],
            countries=None,
            languages_text=["English"],
            release_date="2014-11-07",
            awards=SimpleNamespace(wins=44, nominations=148),
            plot="A team of explorers travel through a wormhole in space.",
            directors=[SimpleNamespace(name="Christopher Nolan")],
            stars=[SimpleNamespace(name="Matthew McConaughey", characters=["Cooper"])],
            worldwide_gross="$733,202,212",
            url="https://www.imdb.com/title/tt0816692/",
            trailers=["https://www.imdb.com/video/vi1586278169/"],
        ),
    )

    c = FakeClient()
    m = FakeMsg("/imdb Interstellar", uid=5, client=c)
    run(imdb_plg.imdb_cmd(c, m))
    assert "𝗜𝗠𝗗𝗕 𝗦𝗘𝗔𝗥𝗖𝗛" in m.replies[-1] and "Interstellar" in m.replies[-1]

    q = FakeQuery("plg_imdb:pick:tt0816692:5", uid=5)
    q.message._client = c
    q.matches = [__import__("re").match(r"^plg_imdb:(pick|back):([\w_-]+):(\d+)$", q.data)]
    run(imdb_plg.imdb_cb(c, q))
    all_calls = " ".join(str(x) for x in c.calls) + " ".join(str(x) for x in q.message.edits)
    assert "Christopher Nolan" in all_calls and "8.7/10" in all_calls and "United States" in all_calls


def test_anilist_plugin_anime_tabs_and_character(monkeypatch):
    from plugins.anilist import plugin as ani_plg

    fake_media = {
        "id": 154587,
        "idMal": 52991,
        "type": "ANIME",
        "format": "TV",
        "status": "FINISHED",
        "episodes": 28,
        "duration": 24,
        "season": "FALL",
        "seasonYear": 2023,
        "averageScore": 91,
        "favourites": 45000,
        "source": "MANGA",
        "genres": ["Adventure", "Drama", "Fantasy"],
        "siteUrl": "https://anilist.co/anime/154587",
        "title": {"romaji": "Sousou no Frieren", "english": "Frieren: Beyond Journey's End", "native": "葬送のフリーレン"},
        "description": "An elf mage reflects on her journey.<br>~![Hidden spoiler]!~",
        "studios": {"nodes": [{"name": "Madhouse"}]},
        "characters": {"edges": [{"role": "MAIN", "node": {"name": {"full": "Frieren"}}}]},
        "relations": {"edges": [{"relationType": "ADAPTATION", "node": {"title": {"english": "Frieren"}, "format": "MANGA"}}]},
    }

    async def fake_gql(gql, variables):
        if "Character(" in gql:
            return {
                "Character": {
                    "id": 1,
                    "name": {"full": "Spike Spiegel", "native": "スパイク", "alternative": ["Swimming Bird"]},
                    "favourites": 32000,
                    "siteUrl": "https://anilist.co/character/1",
                    "image": {"large": "https://example.com/spike.jpg"},
                    "description": "Bounty hunter aboard the Bebop.",
                    "media": {"nodes": [{"title": {"english": "Cowboy Bebop"}, "format": "TV"}]},
                }
            }
        return {"Media": fake_media}

    monkeypatch.setattr(ani_plg, "query_anilist", fake_gql)
    c = FakeClient()
    m = FakeMsg("/anime Frieren", uid=5, client=c)
    run(ani_plg.anime_cmd(c, m))
    sent = c.called("send_web_page") or c.called("send_message")
    text = str(sent[-1][2].get("text") or sent[-1][1][1])
    assert "Madhouse" in text and "91/100" in text and "[Spoiler]" in text

    # Switch to Cast tab
    q = FakeQuery("plg_ani:v:anime:154587:chars:5", uid=5)
    q.message._client = c
    q.matches = [__import__("re").match(r"^plg_ani:v:(\w+):(\d+):(\w+):(\d+)$", q.data)]
    run(ani_plg.anilist_tab_cb(c, q))
    assert any("Frieren" in str(x) for x in c.calls)

    # Character lookup
    mc = FakeMsg("/character Spike Spiegel", uid=5, client=c)
    run(ani_plg.character_cmd(c, mc))
    assert any("Swimming Bird" in str(x) and "ᴄᴏᴡʙᴏʏ ʙᴇʙᴏᴘ" in str(x) for x in c.calls)


def test_mydramalist_and_exportsession_plugins(monkeypatch):
    from plugins.exportsession import plugin as sess_plg
    from plugins.mydramalist import plugin as mdl_plg

    async def fake_search_mdl(q):
        return [{"slug": "702277-alchemy-of-souls", "title": "Alchemy of Souls", "year": 2022, "type": "Drama", "rating": 9.1}]

    async def fake_fetch_mdl(slug):
        return {
            "title": "Alchemy of Souls",
            "poster": "https://example.com/aos.jpg",
            "rating": 9.1,
            "synopsis": "Mages in Daeho face fate twisted by a forbidden spell.",
            "details": {"country": "South Korea", "type": "Drama", "episodes": "20", "aired": "2022", "duration": "1 hr. 20 min."},
            "others": {"genres": ["Action", "Romance", "Fantasy"], "original_network": ["tvN"]},
            "casts": [{"name": "Lee Jae Wook", "role": "Jang Uk"}],
        }

    monkeypatch.setattr(mdl_plg, "search_mdl", fake_search_mdl)
    monkeypatch.setattr(mdl_plg, "fetch_mdl_drama", fake_fetch_mdl)
    c = FakeClient()
    m = FakeMsg("/mdl Alchemy of Souls", uid=5, client=c)
    run(mdl_plg.mdl_cmd(c, m))
    assert any("South Korea" in str(x) and "Jang Uk" in str(x) for x in c.calls)

    # ExportSession security gate: non-owner blocked, owner gets confirmation & spoiler export
    m_non_owner = FakeMsg("/exportsession", uid=5, client=c)
    run(sess_plg.exportsession_cmd(c, m_non_owner))
    assert "Only bot owners" in m_non_owner.replies[-1]

    m_owner = FakeMsg("/exportsession", uid=111, client=c)
    run(sess_plg.exportsession_cmd(c, m_owner))
    assert "Never share a session string" in m_owner.replies[-1]

    c.export_session_string = lambda: __import__("asyncio").sleep(0, result="WZGRAM_TEST_SESSION_STRING_XYZ")
    monkeypatch.setattr(sess_plg, "AUTO_DELETE_SEC", 0)
    q = FakeQuery("plg_sess:export", uid=111)
    q.matches = [__import__("re").match(r"^plg_sess:(export|cancel)$", q.data)]
    run(sess_plg.exportsession_cb(c, q))
    assert "<tg-spoiler><code>WZGRAM_TEST_SESSION_STRING_XYZ</code></tg-spoiler>" in q.message.edits[-1]


# ───────────────────────── 6. Direct URL /mediainfo & Rich /speedtest ─────────────────────────
def test_mediainfo_blocks_ssrf_and_speedtest_masks_ip(monkeypatch):
    from core.tools import _probe_http_header
    from VideoEncoder.plugins.speed_test import build_speedtest_doc, mask_ip, speedtest_handler
    from VideoEncoder.utils.leech import LeechError

    # SSRF guard blocks localhost/private addresses on HTTP Range MediaInfo
    with tempfile.NamedTemporaryFile() as tf:
        with pytest.raises(LeechError):
            run(_probe_http_header("http://127.0.0.1:8080/secret.mkv", tf.name))

    assert mask_ip("203.0.113.45") == "203.0.x.x"
    assert mask_ip("2001:db8:85a3::1") == "2001:db8:xxxx:xxxx"

    sample = {
        "download": 800_000_000,
        "upload": 400_000_000,
        "ping": 8.4,
        "bytes_received": 120_000_000,
        "bytes_sent": 60_000_000,
        "share": "http://www.speedtest.net/result/12345.png",
        "server": {"sponsor": "CloudNet", "name": "Kochi", "country": "India", "d": 12.3},
        "client": {"ip": "198.51.100.77", "isp": "FiberISP", "isprating": "4.8"},
    }
    doc, kb = build_speedtest_doc(sample)
    classic = doc.classic()
    assert "198.51.x.x" in classic and "198.51.100.77" not in classic
    assert "800.00 Mbps" in classic and "CloudNet" in classic

    monkeypatch.setattr("VideoEncoder.plugins.speed_test.run_speedtest", lambda: __import__("asyncio").sleep(0, result=sample))
    c = FakeClient()
    m = FakeMsg("/speedtest", uid=111, client=c)
    run(speedtest_handler(c, m))
    assert any("198.51.x.x" in str(x) and "198.51.100.77" not in str(x) for x in c.calls)
