"""
Bot profile photo — Bot API 9.4 ``setMyProfilePhoto`` / ``removeMyProfilePhoto``.

Owners can change Videl's own avatar from chat:
    /setbotpic   (reply to a photo)   → new profile photo
    /delbotpic                        → remove the current photo

The same helpers are used by the clone dashboard (🤖 Bot Photo) so every clone
owner can give their FileStore bot an avatar without touching @BotFather.
"""
import logging
import os

from pyrogram import Client, filters, raw
from pyrogram.types import Message

from config import OWNERS

log = logging.getLogger("videl.profile")


async def set_bot_photo(client: Client, path: str) -> None:
    """Upload *path* as the profile photo of the bot behind *client*.
    Raises the Telegram error if both methods are rejected."""
    try:
        await client.set_profile_photo(photo=path)
        return
    except Exception as first:  # noqa: BLE001
        log.info(f"set_profile_photo failed ({first}); trying photos.uploadProfilePhoto(bot=self)")
        try:
            uploaded = await client.save_file(path)
            await client.invoke(raw.functions.photos.UploadProfilePhoto(
                file=uploaded, bot=raw.types.InputUserSelf()))
        except Exception:
            raise first


async def remove_bot_photo(client: Client) -> None:
    """Remove the current profile photo of the bot behind *client*."""
    await client.invoke(raw.functions.photos.UpdateProfilePhoto(id=raw.types.InputPhotoEmpty()))


def _photo_source(message: Message):
    target = message.reply_to_message or message
    if target.photo:
        return target
    doc = target.document
    if doc and (doc.mime_type or "").startswith("image/"):
        return target
    return None


async def download_photo(client: Client, message: Message) -> str | None:
    src = _photo_source(message)
    if not src:
        return None
    os.makedirs("downloads", exist_ok=True)
    return await client.download_media(src, file_name=f"downloads/botpic_{message.id}.jpg")


@Client.on_message(filters.command(["setbotpic", "setmypic"]) & filters.user(OWNERS))
async def setbotpic_cmd(client: Client, message: Message):
    if not _photo_source(message):
        return await message.reply_text(
            "<b>🖼 Set bot profile photo</b>\n\nReply to a <b>photo</b> (or an image file) with "
            "<code>/setbotpic</code>.\nUse <code>/delbotpic</code> to remove the current one.")
    status = await message.reply_text("⏳ Uploading new profile photo…")
    path = await download_photo(client, message)
    try:
        await set_bot_photo(client, path)
    except Exception as e:
        return await status.edit_text(f"❌ Telegram rejected the photo:\n<code>{e}</code>")
    finally:
        if path and os.path.exists(path):
            os.remove(path)
    await status.edit_text("✅ <b>Profile photo updated!</b>\n<i>It can take a moment to appear in all clients.</i>")
    from core import botlog
    await botlog.event("BotPhoto", botlog.user_block(message.from_user, "🖼 Bot profile photo changed."), client=client)


@Client.on_message(filters.command(["delbotpic", "delmypic"]) & filters.user(OWNERS))
async def delbotpic_cmd(client: Client, message: Message):
    try:
        await remove_bot_photo(client)
    except Exception as e:
        return await message.reply_text(f"❌ Couldn't remove the photo:\n<code>{e}</code>")
    await message.reply_text("🗑 <b>Profile photo removed.</b>")
