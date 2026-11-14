"""
Secure WzGram Session Exporter Plugin (inspired by WZML-X wzv3-dev plugins/gen_pyro_sess).

Exports the owner's saved user session (from ``/login``) or active WzGram session
string inside a private DM with:
  • Strict OWNER_ID + private-chat gate
  • Explicit ``ButtonStyle.DANGER`` confirmation step
  • ``<tg-spoiler>`` hidden display + 60-second automatic message deletion via ``core.bg.spawn``
"""
from __future__ import annotations

import asyncio
import html
import logging

from pyrogram import Client, enums, filters
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from config import OWNERS
from core import bg, rich
from core.plugin_manager import PluginBase, plugin_enabled, plugin_filter
from core.rich import Doc
from core.ui import copy_button, styled_button

log = logging.getLogger("videl.plugins.exportsession")
AUTO_DELETE_SEC = 60


class Plugin(PluginBase):
    pass


async def _auto_delete(msg, delay: int = AUTO_DELETE_SEC):
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except Exception:
        pass


async def _find_session_string(client: Client, uid: int) -> tuple[str, str]:
    """Return (source_label, session_string) from saver DB or active client."""
    try:
        from database.db import db as saver_db
        s = await saver_db.get_session(uid)
        if s:
            return "User Session (/login)", str(s)
    except Exception:
        pass
    if hasattr(client, "export_session_string"):
        s = await client.export_session_string()
        if s:
            return "Active Bot WzGram Session", str(s)
    raise RuntimeError("No active user or client session string available to export.")


@Client.on_message(filters.command(["exportsession", "gensession"]) & plugin_filter("exportsession"))
async def exportsession_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else 0
    if uid not in OWNERS:
        return await message.reply_text("❌ Only bot owners can use <code>/exportsession</code>.")
    if message.chat.type != enums.ChatType.PRIVATE:
        return await message.reply_text("🔐 For security, run <code>/exportsession</code> in a private DM with the bot.")

    doc = Doc("🔐", "Export WzGram Session", "security confirmation required")
    doc.pull_quote("Never share a session string — anyone holding it has full access to that Telegram session.")
    doc.items([
        "Exports your saved <code>/login</code> user session (or bot session fallback).",
        f"The exported message automatically self-destructs after <b>{AUTO_DELETE_SEC} seconds</b>.",
    ])
    kb = InlineKeyboardMarkup([
        [
            styled_button("⚠️ Export Session Now", callback_data="plg_sess:export", style="danger"),
            styled_button("❌ Cancel", callback_data="plg_sess:cancel", style="success"),
        ]
    ])
    await rich.reply(message, doc, reply_markup=kb)


@Client.on_callback_query(filters.regex(r"^plg_sess:(export|cancel)$"))
async def exportsession_cb(client: Client, query: CallbackQuery):
    if not plugin_enabled("exportsession"):
        return await query.answer("🔌 Session plugin is disabled.", show_alert=True)
    uid = query.from_user.id if query.from_user else 0
    if uid not in OWNERS:
        return await query.answer("❌ Owners only.", show_alert=True)
    op = query.matches[0].group(1)
    if op == "cancel":
        await query.answer("Cancelled.")
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    try:
        label, sess_str = await _find_session_string(client, uid)
    except Exception as e:
        return await query.answer(f"❌ {str(e)[:180]}", show_alert=True)

    await query.answer("🔐 Session exported — auto-deleting in 60s")
    kb_rows = []
    if len(sess_str) <= 256:
        kb_rows.append([copy_button("📋 Copy Session", sess_str, style="primary")])
    kb_rows.append([styled_button("🗑 Delete Immediately", callback_data="plg_sess:cancel", style="danger")])

    text = (
        f"<b>🔐 {html.escape(label)}</b>\n\n"
        f"<tg-spoiler><code>{html.escape(sess_str)}</code></tg-spoiler>\n\n"
        f"<i>⏳ Self-destructs in {AUTO_DELETE_SEC} seconds.</i>"
    )
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb_rows))
    bg.spawn(_auto_delete(query.message, AUTO_DELETE_SEC), name="exportsession_autodel")
