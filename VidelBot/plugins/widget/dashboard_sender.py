"""Outgoing-only aiogram transport for the local dashboard widget."""
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.methods.base import TelegramMethod
from aiogram.types import Message

from core import botapi
from plugins.widget.transport import BETA_BASE


class SendDashboardWidget(TelegramMethod[Message]):
    __returning__ = Message
    __api_method__ = "sendWidget"
    chat_id: int
    html: str
    is_shared: bool = False
    allow_queries: bool = False
    caption: str = ("Videl Dashboard · private statistics snapshot. "
                    "Requires a compatible Telegram client; request /dashboard again to refresh.")


async def send(outgoing, chat_id: int, html: str, mode: str):
    method = SendDashboardWidget(chat_id=chat_id, html=html)
    if mode == "configured":
        return await outgoing(method)
    if mode != "beta":
        raise ValueError("Invalid widget API mode")
    session = AiohttpSession(api=TelegramAPIServer.from_base(BETA_BASE), timeout=botapi.REQUEST_TIMEOUT)
    try:
        return await Bot(outgoing.token, session=session)(method)
    finally:
        await session.close()
