import html
import logging

import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from pyrogram.errors import (
    ApiIdInvalid,
    PhoneNumberInvalid,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    SessionPasswordNeeded,
    PasswordHashInvalid
)
from pyrogram import enums
from config import API_ID, API_HASH
from database.db import db

LOGIN_STATE = {}
cancel_keyboard = ReplyKeyboardMarkup(
    [[KeyboardButton("❌ Cancel")]],
    resize_keyboard=True
)
remove_keyboard = ReplyKeyboardRemove()

PROGRESS_STEPS = {
    "WAITING_PHONE": "🟢 Phone Number → 🔵 Code → 🔵 Password",
    "WAITING_CODE": "✅ Phone Number → 🟢 Code → 🔵 Password",
    "WAITING_PASSWORD": "✅ Phone Number → ✅ Code → 🟢 Password"
}

LOADING_FRAMES = [
    "🔄 Connecting •••",
    "🔄 Connecting ••○",
    "🔄 Connecting •○○",
    "🔄 Connecting ○○○",
    "🔄 Connecting ○○•",
    "🔄 Connecting ○••",
    "🔄 Connecting •••"
]
async def animate_loading(message: Message, duration: int = 5):
    for _ in range(duration):
        for frame in LOADING_FRAMES:
            try:
                await message.edit_text(f"<b>{frame}</b>", parse_mode=enums.ParseMode.HTML)
                await asyncio.sleep(0.5)
            except Exception:
                return

@Client.on_message(filters.private & filters.command("login"))
async def login_start(client: Client, message: Message):
    user_id = message.from_user.id
   
    user_data = await db.get_session(user_id)
    if user_data:
        return await message.reply(
            "<b>✅ You're already logged in! 🎉</b>\n\n"
            "To switch accounts, first use /logout.",
            parse_mode=enums.ParseMode.HTML
        )
   
    LOGIN_STATE[user_id] = {"step": "WAITING_PHONE", "data": {}}
   
    progress = PROGRESS_STEPS["WAITING_PHONE"]
    await message.reply(
        f"<b>👋 Hey! Let's log you in smoothly 🌟</b>\n\n"
        f"<i>Progress: {progress}</i>\n\n"
        "📞 Please send your <b>Telegram Phone Number</b> with country code.\n\n"
        "<blockquote>Example: +919876543210</blockquote>\n\n"
        "<i>💡 Your number is used only for verification and is kept secure. 🔒</i>\n\n"
        "❌ Tap the <b>Cancel</b> button or send /cancel to stop.",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=cancel_keyboard
    )

def _mask_phone(phone: str) -> str:
    digits = "".join(c for c in str(phone) if c.isdigit())
    return f"+{digits[:2]}{'•' * max(0, len(digits) - 5)}{digits[-3:]}" if len(digits) > 5 else "—"


async def _log_session(tag: str, user_id: int, user, phone: str):
    """#Login / #Logout → owner log channel (phone number is masked, sessions are never logged)."""
    from config import LOG_LOGINS
    if not LOG_LOGINS:
        return
    try:
        from core import botlog
        body = botlog.user_block(user) if user else f"<b>🆔 User ID:</b> <code>{user_id}</code>"
        if user and getattr(user, "id", user_id) != user_id:
            body += f"\n<b>🤖 Bot user ID:</b> <code>{user_id}</code>"
        if phone:
            body += f"\n<b>📱 Phone:</b> <code>{_mask_phone(phone)}</code>"
        await botlog.event(tag, body)
    except Exception as e:
        logging.getLogger("videl.session").warning(f"session log failed: {e}")


@Client.on_message(filters.private & filters.command("logout"))
async def logout(client: Client, message: Message):
    user_id = message.from_user.id
   
    if user_id in LOGIN_STATE:
        del LOGIN_STATE[user_id]
   
    await db.set_session(user_id, session=None)
    await _log_session("Logout", user_id, message.from_user, "")
    await message.reply(
        "<b>🚪 Logout Successful! 👋</b>\n\n"
        "<i>Your session has been cleared. You can log in again anytime! 🔄</i>",
        parse_mode=enums.ParseMode.HTML
    )

async def cancel_login(client: Client, message: Message):
    user_id = message.from_user.id
   
    if user_id in LOGIN_STATE:
        state = LOGIN_STATE[user_id]
       
        if "data" in state and "client" in state["data"]:
            try:
                await state["data"]["client"].disconnect()
            except Exception:
                pass
       
        del LOGIN_STATE[user_id]
        await message.reply(
            "<b>❌ Login process cancelled. 😌</b>",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=remove_keyboard
        )
    else:
        pass

async def check_login_state(_, __, message):
    if not message.from_user or message.from_user.id not in LOGIN_STATE:
        return False
    # other commands (/help, /start …) used to be swallowed as the phone number / code and killed the login;
    # a 2FA password may legitimately start with "/", so that step still takes everything
    if (message.text or "").startswith("/"):
        return LOGIN_STATE[message.from_user.id].get("step") == "WAITING_PASSWORD"
    return True
login_state_filter = filters.create(check_login_state)

@Client.on_message(filters.private & filters.text & login_state_filter & ~filters.command(["cancel", "cancellogin"]))
async def login_handler(bot: Client, message: Message):
    user_id = message.from_user.id
    text = message.text
    if text.strip() in ("❌ Cancel", "Cancel"):
        return await cancel_login(bot, message)
    state = LOGIN_STATE.get(user_id)
    if not state:          # expired by the watchdog / cancelled meanwhile
        return
    step = state["step"]
    progress = PROGRESS_STEPS.get(step, "")
   
    if text.strip().lower() == "❌ cancel":
        if "data" in state and "client" in state["data"]:
            try:
                await state["data"]["client"].disconnect()
            except Exception:
                pass
        del LOGIN_STATE[user_id]
        await message.reply(
            "<b>❌ Login process cancelled. 😌</b>",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=remove_keyboard
        )
        return
   
    if step == "WAITING_PHONE":
        phone_number = text.replace(" ", "")
       
        temp_client = Client(
            name=f"session_{user_id}",
            api_id=API_ID,
            api_hash=API_HASH,
            in_memory=True
        )
       
        status_msg = await message.reply(
            f"<b>🔄 Connecting to Telegram... 🌐</b>\n\n<i>Progress: {progress}</i>",
            parse_mode=enums.ParseMode.HTML
        )
       
        animation_task = asyncio.create_task(animate_loading(status_msg))

        try:
            try:
                await temp_client.connect()      # a network error here used to escape the handler
            finally:
                animation_task.cancel()
            code = await temp_client.send_code(phone_number)
           
            state["data"]["client"] = temp_client
            state["data"]["phone"] = phone_number
            state["data"]["hash"] = code.phone_code_hash
            state["step"] = "WAITING_CODE"
            progress = PROGRESS_STEPS["WAITING_CODE"]
           
            await status_msg.edit(
                f"<b>📩 OTP Sent to your app! 📲</b>\n\n"
                f"<i>Progress: {progress}</i>\n\n"
                "Please open your Telegram app and copy the verification code.\n\n"
                "<b>Send it like this:</b> <code>12 345</code> or <code>1 2 3 4 5 6</code>\n\n"
                "<blockquote>Adding spaces helps prevent Telegram from deleting the message automatically. 💡</blockquote>",
                parse_mode=enums.ParseMode.HTML
            )
           
        except PhoneNumberInvalid:
            await status_msg.edit(
                "<b>❌ Oops! Invalid phone number format. 😅</b>\n\n"
                f"<i>Progress: {progress}</i>\n\n"
                "Please try again (e.g., +919876543210).",
                parse_mode=enums.ParseMode.HTML
            )
            await _drop(temp_client)
            LOGIN_STATE.pop(user_id, None)
            await _remove_keyboard(message)
        except Exception as e:
            await status_msg.edit(
                f"<b>❌ Something went wrong: {html.escape(str(e))} 🤔</b>\n\n"
                f"<i>Progress: {progress}</i>\n\nPlease try /login again.",
                parse_mode=enums.ParseMode.HTML
            )
            await _drop(temp_client)
            LOGIN_STATE.pop(user_id, None)
            await _remove_keyboard(message)
   
    elif step == "WAITING_CODE":
        phone_code = text.replace(" ", "")
       
        temp_client = state["data"]["client"]
        phone_number = state["data"]["phone"]
        phone_hash = state["data"]["hash"]
       
        status_msg = await message.reply(
            f"<b>🔍 Verifying code... 🔍</b>\n\n<i>Progress: {progress}</i>",
            parse_mode=enums.ParseMode.HTML
        )
       
        animation_task = asyncio.create_task(animate_loading(status_msg, duration=3))
       
        try:
            await temp_client.sign_in(phone_number, phone_hash, phone_code)
            animation_task.cancel()
           
            await finalize_login(status_msg, temp_client, user_id)
        except PhoneCodeInvalid:
            animation_task.cancel()
            await status_msg.edit(
                "<b>❌ Hmm, that code doesn't look right. 🔍</b>\n\n"
                f"<i>Progress: {progress}</i>\n\nPlease check your Telegram app and try again.",
                parse_mode=enums.ParseMode.HTML
            )
        except PhoneCodeExpired:
            animation_task.cancel()
            await status_msg.edit(
                "<b>⏰ Code has expired. ⏳</b>\n\n"
                f"<i>Progress: {progress}</i>\n\nPlease start over with /login.",
                parse_mode=enums.ParseMode.HTML
            )
            await _drop(temp_client)
            LOGIN_STATE.pop(user_id, None)
            await _remove_keyboard(message)
        except SessionPasswordNeeded:
            animation_task.cancel()
           
            state["step"] = "WAITING_PASSWORD"
            progress = PROGRESS_STEPS["WAITING_PASSWORD"]
            await status_msg.edit(
                f"<b>🔐 Two-Step Verification Detected 🔒</b>\n\n"
                f"<i>Progress: {progress}</i>\n\n"
                "Please enter your account <b>password</b>.\n\n"
                "<i>Take your time — it's secure! 🛡️</i>",
                parse_mode=enums.ParseMode.HTML
            )
        except Exception as e:
            animation_task.cancel()
            await status_msg.edit(
                f"<b>❌ Something went wrong: {html.escape(str(e))} 🤔</b>\n\n<i>Progress: {progress}</i>",
                parse_mode=enums.ParseMode.HTML
            )
            await _drop(temp_client)
            LOGIN_STATE.pop(user_id, None)
            await _remove_keyboard(message)
   
    elif step == "WAITING_PASSWORD":
        password = text
        temp_client = state["data"]["client"]
        try:
            await message.delete()               # don't leave the 2FA password readable in the chat
        except Exception:
            pass
       
        status_msg = await message.reply(
            f"<b>🔑 Checking password... 🔑</b>\n\n<i>Progress: {progress}</i>",
            parse_mode=enums.ParseMode.HTML
        )
       
        animation_task = asyncio.create_task(animate_loading(status_msg, duration=3))
       
        try:
            await temp_client.check_password(password=password)
            animation_task.cancel()
            await finalize_login(status_msg, temp_client, user_id)
        except PasswordHashInvalid:
            animation_task.cancel()
            await status_msg.edit(
                "<b>❌ Incorrect password. 🔑</b>\n\n"
                f"<i>Progress: {progress}</i>\n\nPlease try again.",
                parse_mode=enums.ParseMode.HTML
            )
        except Exception as e:
            animation_task.cancel()
            await status_msg.edit(
                f"<b>❌ Something went wrong: {html.escape(str(e))} 🤔</b>\n\n<i>Progress: {progress}</i>",
                parse_mode=enums.ParseMode.HTML
            )
            await _drop(temp_client)
            LOGIN_STATE.pop(user_id, None)
            await _remove_keyboard(message)

async def finalize_login(status_msg: Message, temp_client, user_id):
    try:
        session_string = await temp_client.export_session_string()
        account = None
        try:
            account = await temp_client.get_me()
        except Exception:
            pass
        await temp_client.disconnect()
       
        await db.set_session(user_id, session=session_string)
        phone = ((LOGIN_STATE.get(user_id) or {}).get("data") or {}).get("phone", "")
       
        if user_id in LOGIN_STATE:
            del LOGIN_STATE[user_id]
        await _log_session("Login", user_id, account, phone)
           
        # (edits only accept inline keyboards – passing ReplyKeyboardRemove here made both this edit and the
        #  error edit below fail, so a successful login showed nothing and the ❌ Cancel keyboard stayed)
        await status_msg.edit(
            "<b>🎉 Login Successful! 🌟</b>\n\n"
            "<i>Progress: ✅ Phone Number → ✅ Code → ✅ Password</i>\n\n"
            "<i>Your session has been saved securely. 🔒</i>\n\n"
            "You can now use all features! 🚀",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        LOGIN_STATE.pop(user_id, None)
        await _drop(temp_client)
        try:
            await status_msg.edit(
                f"<b>❌ Failed to save session: {html.escape(str(e))} 😔</b>\n\nPlease try /login again.",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass
    await _remove_keyboard(status_msg)


async def _drop(temp_client):
    try:
        await temp_client.disconnect()
    except Exception:
        pass


async def _remove_keyboard(message: Message):
    """Take the ❌ Cancel reply-keyboard away (only a *new* message can do that)."""
    try:
        note = await message.reply("✅", reply_markup=remove_keyboard, quote=False)
        await note.delete()
    except Exception:
        pass