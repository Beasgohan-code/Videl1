

from pyrogram import Client, filters
from pyrogram.types import Message

from .. import all
from ..utils.database.access_db import db
from ..utils.database.add_user import AddUserToDatabase
from ..utils.helper import check_chat, output
from ..utils.settings import OpenSettings


@Client.on_message(filters.command("reset"))
async def reset(bot: Client, update: Message):
    c = await check_chat(update, chat='Both')
    if not c:
        return
    await db.delete_user(update.from_user.id)
    await db.add_user(update.from_user.id)
    await update.reply(text="♻️ <b>Encoder settings reset to the defaults.</b>", reply_markup=output)


async def settings_handler(bot: Client, event: Message):
    c = await check_chat(event, chat='Both')
    if not c:
        return
    await AddUserToDatabase(bot, event)
    editable = await event.reply_text("Please Wait ...")
    await OpenSettings(editable, user_id=event.from_user.id)


@Client.on_message(filters.command("vset"))
async def settings_viewer(bot: Client, event: Message):
    c = await check_chat(event, chat='Both')
    if c is None:
        return
    await AddUserToDatabase(bot, event)
    # User ID
    if event.reply_to_message and event.reply_to_message.from_user:
        user_id = event.reply_to_message.from_user.id
    elif not event.reply_to_message and len(event.command) == 1:
        user_id = event.from_user.id
    elif not event.reply_to_message and len(event.command) != 1:
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
    await rich.reply(event, vset_doc(user_id, await db.get_settings(user_id)))


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
    return doc.footer("Change them with /settings → Encoder.")
