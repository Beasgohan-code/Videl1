"""Outgoing-only aiogram sender for the local tools widget."""
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.methods.base import TelegramMethod
from aiogram.types import Message

from core import botapi
from plugins.widget.transport import BETA_BASE


class SendToolsWidget(TelegramMethod[Message]):
    __returning__ = Message
    __api_method__ = "sendWidget"
    chat_id: int
    html: str
    message_thread_id: int | None = None
    caption: str = "Videl tools: bill splitter, unit converter and counter. Requires a compatible Telegram client."


async def send(outgoing, chat_id: int, thread_id: int | None, html: str, mode: str):
    method = SendToolsWidget(chat_id=chat_id, html=html, message_thread_id=thread_id)
    if mode == "configured":
        return await outgoing(method)
    if mode != "beta":
        raise ValueError("Invalid widget API mode")
    session = AiohttpSession(api=TelegramAPIServer.from_base(BETA_BASE), timeout=botapi.REQUEST_TIMEOUT)
    try:
        return await Bot(outgoing.token, session=session)(method)
    finally:
        await session.close()
