import html
import io

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from core.ui import styled_button as btn
from .. import all
from ..utils.database.access_db import db
from ..utils.database.add_user import AddUserToDatabase
from ..utils.helper import check_chat, output
from ..utils.settings import OpenSettings


@Client.on_message(filters.command("reset"))
async def reset(bot: Client, update: Message):
    c = await check_chat(update, chat="Both")
    if not c:
        return
    await db.delete_user(update.from_user.id)
    await db.add_user(update.from_user.id)
    await update.reply(text="♻️ <b>Encoder settings reset to the defaults.</b>", reply_markup=output)


async def settings_handler(bot: Client, event: Message):
    c = await check_chat(event, chat="Both")
    if not c:
        return
    await AddUserToDatabase(bot, event)
    editable = await event.reply_text("Please Wait ...")
    await OpenSettings(editable, user_id=event.from_user.id)


def _vset_kb(user_id: int, viewer_id: int) -> InlineKeyboardMarkup | None:
    if int(user_id) != int(viewer_id):
        return None
    return InlineKeyboardMarkup([
        [
            btn("📤 Export JSON", callback_data=f"uset:exp:{user_id}", style="primary"),
            btn("♻️ Reset…", callback_data=f"uset:ask:{user_id}", style="danger"),
        ]
    ])


@Client.on_message(filters.command("vset"))
async def settings_viewer(bot: Client, event: Message):
    c = await check_chat(event, chat="Both")
    if c is None:
        return
    await AddUserToDatabase(bot, event)
    caller_id = event.from_user.id

    # Subcommands: `/vset export` or `/vset import` (reply to JSON document)
    sub = event.command[1].lower() if len(event.command) >= 2 else ""
    if sub == "export":
        from core.settings_portal import export_user_settings
        raw_bytes = await export_user_settings(caller_id)
        bio = io.BytesIO(raw_bytes)
        bio.name = f"videl_settings_{caller_id}.json"
        return await event.reply_document(
            bio,
            caption=(
                "📤 <b>Videl Portable Settings Export</b>\n"
                "<i>Reply <code>/vset import</code> to this file anytime to restore your Encoder, "
                "Auto-Rename, and Saver preferences (zero secrets included).</i>"
            ),
        )
    if sub == "import":
        rep = event.reply_to_message
        doc_media = getattr(rep, "document", None) if rep else None
        if not doc_media:
            return await event.reply_text(
                "📥 Reply <code>/vset import</code> to a <code>.json</code> file exported by <code>/vset export</code>."
            )
        from core.settings_portal import MAX_SETTINGS_BYTES, SettingsPortalError, import_user_settings
        if int(getattr(doc_media, "file_size", 0) or 0) > MAX_SETTINGS_BYTES:
            return await event.reply_text("❌ Settings file is too large (max 256 KiB).")
        path = await bot.download_media(rep, in_memory=True)
        raw_data = path.getvalue() if hasattr(path, "getvalue") else (open(path, "rb").read() if path else b"")
        try:
            rep_info = await import_user_settings(caller_id, raw_data)
        except SettingsPortalError as err:
            return await event.reply_text(f"❌ <b>Import rejected:</b> <code>{html.escape(str(err))}</code>")
        skipped_note = (
            f"\n<i>⚠️ Ignored {len(rep_info['skipped'])} unsupported/protected key(s).</i>"
            if rep_info["skipped"]
            else ""
        )
        return await event.reply_text(
            f"✅ <b>Imported {rep_info['applied']} preference(s)</b> "
            f"(Encoder: {len(rep_info['encoder_keys'])}, Auto-Rename: {len(rep_info['renamer_keys'])}, "
            f"Saver: {len(rep_info['saver_keys'])}).{skipped_note}"
        )

    # User ID / @username lookup
    if event.reply_to_message and event.reply_to_message.from_user and len(event.command) == 1:
        user_id = event.reply_to_message.from_user.id
    elif not event.reply_to_message and len(event.command) == 1:
        user_id = caller_id
    elif len(event.command) != 1:
        arg = event.command[1]
        if arg.lstrip("-").isdigit():
            user_id = int(arg)
        else:
            try:
                user_id = (await bot.get_users(arg)).id
            except Exception:
                return await event.reply_text("❌ Send a user ID / @username, or reply to the user.")
    else:
        return

    from core import rich
    await rich.reply(
        event,
        vset_doc(user_id, await db.get_settings(user_id)),
        reply_markup=_vset_kb(user_id, caller_id),
    )


@Client.on_callback_query(filters.regex(r"^uset:(exp|ask|ok|back):\d+$"))
async def user_settings_portal_cb(bot: Client, query: CallbackQuery):
    _, action, target_str = (query.data or "").split(":", 2)
    target_uid = int(target_str)
    caller_id = getattr(getattr(query, "from_user", None), "id", 0)
    if caller_id != target_uid:
        return await query.answer("Only the owner of these settings can use this control.", show_alert=True)

    if action == "exp":
        from core.settings_portal import export_user_settings
        raw_bytes = await export_user_settings(caller_id)
        bio = io.BytesIO(raw_bytes)
        bio.name = f"videl_settings_{caller_id}.json"
        await query.answer("📤 Sending settings export…")
        return await query.message.reply_document(
            bio,
            caption="📤 <b>Videl Portable Settings Export</b>\n<i>Reply <code>/vset import</code> to restore.</i>",
        )

    if action == "ask":
        kb = InlineKeyboardMarkup([
            [
                btn("✅ Confirm Reset", callback_data=f"uset:ok:{target_uid}", style="danger"),
                btn("↩️ Cancel", callback_data=f"uset:back:{target_uid}"),
            ]
        ])
        await query.answer()
        return await query.message.edit_text(
            "⚠️ <b>Reset all portable preferences?</b>\n\n"
            "This resets your Encoder settings, Auto-Rename template/metadata, and Saver caption/word-filters "
            "back to defaults.\n<i>Your account, Premium status, and /login session are kept untouched.</i>",
            reply_markup=kb,
        )

    if action == "ok":
        from core import rich
        from core.settings_portal import reset_user_preferences
        await reset_user_preferences(caller_id)
        await query.answer("♻️ Preferences reset to defaults.")
        return await rich.edit(
            query.message,
            vset_doc(caller_id, await db.get_settings(caller_id)),
            reply_markup=_vset_kb(caller_id, caller_id),
        )

    if action == "back":
        from core import rich
        await query.answer("Cancelled.")
        return await rich.edit(
            query.message,
            vset_doc(caller_id, await db.get_settings(caller_id)),
            reply_markup=_vset_kb(caller_id, caller_id),
        )


def vset_doc(user_id: int, s: dict):
    """/vset – every encode setting of a user in one table (one DB read instead of 20+)."""
    from core.rich import Doc
    from ..utils import ffcmd
    from ..utils.display_progress import humanbytes
    d = ffcmd.describe(s)
    key = ffcmd.matches_profile(s)
    doc = Doc("🎬", "Encode settings", f"User {user_id} · profile: {ffcmd.PROFILES[key][0] if key else 'Custom'}")
    doc.h("🎞", "Video")
    doc.table([("Codec", d["codec"]), ("Quality", d["quality"]), ("Resolution", d["resolution"]),
               ("Preset", d["preset"]), ("Tune", d["tune"]), ("FPS", d["fps"]),
               ("Aspect", "16:9" if s["aspect"] else "Source"),
               ("CABAC / Reframe", f"{'On' if s['cabac'] else 'Off'} / {str(s['reframe']).capitalize()}"),
               ("Container", d["container"])], header=("Setting", "Value"))
    doc.h("🔊", "Audio")
    doc.table([("Codec", d["audio"]), ("Bitrate", d["audio_bitrate"]), ("Channels", d["channels"]),
               ("Sample rate", d["sample_rate"])], header=("Setting", "Value"))
    doc.h("🧩", "Extras")
    doc.table([("Filters", d["filters"]), ("Subtitles", d["subtitles"]), ("Watermark", d["watermark"]),
               ("Motion opacity", f"{s['motion_opacity']}%"), ("Upload", d["upload"])], header=("Setting", "Value"))
    if s.get("enc_count"):
        doc.text(f"📈 <b>{s['enc_count']}</b> encodes · <b>{humanbytes(max(0, s['enc_in'] - s['enc_out'])) or '0 B'}</b> saved")
    return doc.footer("Change them with /settings → Encoder · /vset export · /vset import.")
