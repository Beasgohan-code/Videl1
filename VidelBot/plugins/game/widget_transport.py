"""Outgoing-only aiogram transport for the Widget Lab beta API.

This never polls or registers a webhook. Beta uses a separate, short-lived
session so other bot API calls and their configured endpoint are unaffected.
"""
from __future__ import annotations

BETA_BASE = "https://api.telegram.org/beta"


async def send(outgoing, method, mode: str, timeout: int):
    if mode == "configured":
        return await outgoing(method)
    if mode != "beta":
        raise ValueError("invalid widget transport mode")
    from aiogram import Bot
    from aiogram.client.session.aiohttp import AiohttpSession
    from aiogram.client.telegram import TelegramAPIServer

    session = AiohttpSession(api=TelegramAPIServer.from_base(BETA_BASE), timeout=timeout)
    try:
        beta_bot = Bot(outgoing.token, session=session)
        return await beta_bot(method)
    finally:
        # Avoid leaking either the session or secret-bearing exception text.
        try:
            await session.close()
        except Exception:
            pass
