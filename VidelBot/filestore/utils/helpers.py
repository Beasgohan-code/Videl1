
import base64
import asyncio
import aiohttp
import os
from pyrogram import Client
from pyrogram.types import Message
from filestore.fs_config import LOGGER

log = LOGGER(__name__)

main_bot_client = None

# =============================================================================
# DEEP LINK ENCODING / DECODING
# =============================================================================
async def encode(string: str) -> str:
    """Encode a string to URL-safe base64 (without padding)."""
    string_bytes = string.encode("ascii")
    base64_bytes = base64.urlsafe_b64encode(string_bytes)
    return base64_bytes.decode("ascii").strip("=")

async def decode(base64_string: str) -> str:
    """Decode a URL-safe base64 string (handles missing padding)."""
    base64_string = base64_string.strip("=")
    padded = base64_string + "=" * (-len(base64_string) % 4)
    base64_bytes = padded.encode("ascii")
    string_bytes = base64.urlsafe_b64decode(base64_bytes)
    return string_bytes.decode("ascii")


# =============================================================================
# TIME FORMATTING
# =============================================================================

def get_readable_time(seconds: int) -> str:
    """Convert seconds into a human-readable time string (e.g., '2h:30m:15s')."""
    count = 0
    up_time = ""
    time_list = []
    time_suffix_list = ["s", "m", "h", "days"]
    while count < 4:
        count += 1
        remainder, result = divmod(seconds, 60) if count < 3 else divmod(seconds, 24)
        if seconds == 0 and remainder == 0:
            break
        time_list.append(int(result))
        seconds = int(remainder)
    for x in range(len(time_list)):
        time_list[x] = str(time_list[x]) + time_suffix_list[x]
    if len(time_list) == 4:
        up_time += f"{time_list.pop()}, "
    time_list.reverse()
    up_time += ":".join(time_list)
    return up_time


def get_exp_time(seconds: int) -> str:
    """Convert seconds into a readable expiry string (e.g., '2 hours 30 mins')."""
    periods = [("days", 86400), ("hours", 3600), ("mins", 60), ("secs", 1)]
    result = ""
    for period_name, period_seconds in periods:
        if seconds >= period_seconds:
            period_value, seconds = divmod(seconds, period_seconds)
            result += f"{int(period_value)} {period_name} "
    return result.strip()


# =============================================================================
# BOT TOKEN VALIDATION
# =============================================================================

async def validate_bot_token(token: str) -> dict | None:
    """
    Validate a Telegram bot token by calling getMe.

    Returns the bot info dict on success, None on failure.
    """
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("ok"):
                        return data["result"]
                return None
    except asyncio.TimeoutError:
        log.warning("Bot token validation timed out")
        return None
    except Exception as e:
        log.error(f"Bot token validation error: {e}")
        return None


# =============================================================================
# MESSAGE HELPER
# =============================================================================

async def get_message_id(client: Client, message: Message, log_channel_id: int):
    """
    Extract a message ID from a message (forwarded or link) or forward it
    to the log channel if it's new.
    """
    # 1. Check if it's forwarded from the log channel
    forward_chat_id = None
    forward_msg_id = None

    if hasattr(message, "forward_origin"):
        origin = getattr(message, "forward_origin", None)
        if origin:
            if getattr(origin, "chat", None):
                forward_chat_id = origin.chat.id
            elif getattr(origin, "sender_chat", None):
                forward_chat_id = origin.sender_chat.id
            forward_msg_id = getattr(origin, "message_id", None)
    else:
        # Older versions without forward_origin
        ff_chat = getattr(message, "forward_from_chat", None)
        if ff_chat:
            forward_chat_id = ff_chat.id
            forward_msg_id = getattr(message, "forward_from_message_id", None)

    if forward_chat_id == log_channel_id:
        return forward_msg_id

    # 2. Check if it's a link to a message in the log channel
    if message.text:
        import re
        # Pattern for both public and private channel links
        # https://t.me/c/123456789/123 or https://t.me/channel_username/123
        pattern = r"https?://t\.me/(?:c/)?([^/]+)/(\d+)"
        match = re.search(pattern, message.text)
        if match:
            chat_id_str = match.group(1)
            msg_id = int(match.group(2))

            # Convert channel ID string to int if necessary
            target_chat_id = None
            if chat_id_str.isdigit():
                target_chat_id = int("-100" + chat_id_str)

            # Compare with log_channel_id
            if target_chat_id == log_channel_id:
                return msg_id

            # Also check by username if it's a public link
            try:
                chat = await client.get_chat(log_channel_id)
                if chat.username and chat.username.lower() == chat_id_str.lower():
                    return msg_id
            except Exception:
                pass

    # 3. If it's a new message (not forwarded from log), forward it there to store it
    # But only if it has media or text (not just any random service message)
    if message.service:
        return None

    try:
        # We use copy instead of forward to avoid 'Forwarded from' tag if desired,
        # but the prompt says "forwarding file from private".
        # Usually, for a FileStore, we forward to the log channel.
        forwarded = await message.forward(log_channel_id)
        if forwarded:
            return forwarded.id
        return None
    except Exception as e:
        log.error(f"Error forwarding message to log channel {log_channel_id}: {e}")
        return None


async def get_messages(client, channel_id: int, message_ids: list):
    """Fetch messages from a channel, handling FloodWait."""
    from pyrogram.errors import FloodWait

    messages = []
    total = 0
    while total < len(message_ids):
        batch = message_ids[total : total + 200]
        try:
            msgs = await client.get_messages(chat_id=channel_id, message_ids=batch)
        except FloodWait as e:
            await asyncio.sleep(e.value)
            msgs = await client.get_messages(chat_id=channel_id, message_ids=batch)
        except Exception as e:
            log.error(f"Error fetching messages: {e}")
            msgs = []
        total += len(batch)
        if isinstance(msgs, list):
            messages.extend(msgs)
        else:
            messages.append(msgs)
    return messages


# =============================================================================
# IMAGE UPLOAD HELPER
# =============================================================================

async def upload_to_telegraph(client: Client, message: Message) -> str | None:
    """Download a photo/document from a message and upload to freeimage.host."""
    try:
        if not message.photo and not message.document:
            return None

        temp_msg = await message.reply("⏳ <i>Uploading image...</i>")
        file_path = await client.download_media(message)

        if not file_path:
            await temp_msg.edit("❌ Failed to download media.")
            return None

        from core.ui import upload_to_host
        url = await upload_to_host(file_path)
        if url:
            await temp_msg.delete()
            return url
        await temp_msg.edit("❌ Failed to upload image.")
        return None
    except Exception as e:
        log.error(f"Error uploading image: {e}")
        return None
    finally:
        if 'file_path' in locals() and file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except:
                pass

async def send_main_log(client: Client, text: str, tag: str = "LinkGenerated"):
    """
    Send a log message to the owner log channel (routed through core.botlog so it is
    tagged, timestamped, flood-safe and sent by the main bot).
    """
    from config import LOG_LINKS
    if tag == "LinkGenerated" and not LOG_LINKS:
        return
    try:
        from core import botlog
        await botlog.raw(text, client=main_bot_client or client, tag=tag)
    except Exception as e:
        log.error(f"Failed to send main log: {e}")
