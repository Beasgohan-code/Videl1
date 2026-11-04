"""Opt-in per-bot, per-group message counters; never stores message content."""
from __future__ import annotations

import logging
import time

from pymongo.errors import DuplicateKeyError
from pyrogram import Client, enums, filters
from pyrogram.errors import MessageNotModified
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from core import rich
from core.db import vdb
from core.plugin_manager import PluginBase, plugin_enabled, plugin_filter
from core.rich import Doc
from core.ui import styled_button
from plugins.group_mod import plugin as gmod

log = logging.getLogger("videl.plugin.group_activity")
PLG_FILTER = plugin_filter("group_activity")
_SETTINGS = "group_activity_settings"
_MEMBERS = "group_activity_members"


class Plugin(PluginBase):
    async def startup(self, client=None):
        try:
            await vdb.db[_MEMBERS].create_index(
                [("bot_id", 1), ("chat_id", 1), ("opted_out", 1), ("messages", -1), ("user_id", 1)],
                name="activity_rank",
            )
        except Exception:
            # Index failure affects performance, not permission or counting decisions.
            log.warning("Could not initialize group activity ranking index")


def _scope(client, message):
    bot_id = getattr(getattr(client, "me", None), "id", None)
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    if type(bot_id) is not int or bot_id <= 0 or type(chat_id) is not int or chat_id >= 0:
        return None
    return bot_id, chat_id


def _group_key(bot_id, chat_id):
    return f"{bot_id}:{chat_id}"


def _member_key(bot_id, chat_id, user_id):
    return f"{bot_id}:{chat_id}:{user_id}"


def _name(user):
    name = " ".join(str(getattr(user, "first_name", "") or "Member").split())
    return name[:64] or "Member"


async def _enabled(bot_id, chat_id):
    cfg = await vdb.db[_SETTINGS].find_one({"_id": _group_key(bot_id, chat_id)})
    return bool(cfg and cfg.get("enabled") is True)


def _query(bot_id, chat_id):
    return {"bot_id": bot_id, "chat_id": chat_id, "opted_out": {"$ne": True}, "messages": {"$gt": 0}}


_DAY_SECONDS = 86400
_DAYS_RETAINED = 7


def _utc_day():
    """UTC calendar day number at processing time; never message content."""
    return int(time.time() // _DAY_SECONDS)


def _period_expression(day: int, days: int):
    """Mongo expression for complete UTC-date buckets, including today."""
    values = []
    for offset in range(days):
        expected = day - offset
        slot = f"daily.s{expected % _DAYS_RETAINED}"
        values.append({"$cond": [
            {"$eq": [f"${slot}.day", expected]},
            {"$ifNull": [f"${slot}.count", 0]}, 0,
        ]})
    return {"$add": values}


def _period_totals(record: dict, day: int) -> tuple[int, int]:
    daily = record.get("daily") or {}
    amounts = []
    for offset in range(_DAYS_RETAINED):
        expected = day - offset
        bucket = daily.get(f"s{expected % _DAYS_RETAINED}") or {}
        count = bucket.get("count") if bucket.get("day") == expected else 0
        amounts.append(count if type(count) is int and count > 0 else 0)
    return amounts[0], sum(amounts)


async def _count_message(bot_id, chat_id, user, day):
    """Update lifetime and current bucket together, respecting opt-out.

    Reusing a bucket is guarded by its day and bounded retries so concurrent
    first messages cannot reset one another. A very late event may contribute
    only to lifetime if a newer day already owns that slot.
    """
    uid = user.id
    col = vdb.db[_MEMBERS]
    key = _member_key(bot_id, chat_id, uid)
    base = {"_id": key, "opted_out": {"$ne": True}}
    slot = f"daily.s{day % _DAYS_RETAINED}"
    for _ in range(5):
        same = await col.update_one(
            {**base, f"{slot}.day": day},
            {"$set": {"display_name": _name(user)}, "$inc": {"messages": 1, f"{slot}.count": 1}},
        )
        if same.matched_count:
            return
        try:
            rotated = await col.update_one(
                {**base, "$or": [{f"{slot}.day": {"$exists": False}}, {f"{slot}.day": {"$lt": day}}]},
                {"$setOnInsert": {"bot_id": bot_id, "chat_id": chat_id, "user_id": uid},
                 "$set": {"display_name": _name(user), slot: {"day": day, "count": 1}},
                 "$inc": {"messages": 1}},
                upsert=True,
            )
            if rotated.matched_count or rotated.upserted_id is not None:
                return
        except DuplicateKeyError:
            # Another event or an opt-out may have won the insertion/rotation.
            current = await col.find_one({"_id": key}, {"opted_out": 1, slot: 1})
            if current and current.get("opted_out") is True:
                return
            bucket = (current or {}).get("daily", {}).get(f"s{day % _DAYS_RETAINED}", {})
            if isinstance(bucket.get("day"), int) and bucket["day"] > day:
                break  # A newer UTC day owns this slot; lifetime only.
    # Do not reset a newer bucket, or recreate a privacy tombstone. In rare
    # contention or late-delivery cases, record lifetime only.
    await col.update_one(base, {"$set": {"display_name": _name(user)}, "$inc": {"messages": 1}})


async def _error(message):
    return await rich.reply(message, Doc("⚠️", "Activity Unavailable").status("warning", "Could not read or save activity", "Try again later. No statistics are estimated."))


@Client.on_message(filters.group & filters.command("activity") & PLG_FILTER)
async def activity_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else None
    ok, err = await gmod.verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_restrict_members",
        is_anon_admin=gmod._is_anon_group_admin(message), check_bot=False,
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Group Activity").status("error", "Permission check failed", err))
    scope = _scope(client, message)
    if not scope:
        return await _error(message)
    bot_id, chat_id = scope
    args = (message.command or [])[1:]
    if args and (len(args) != 1 or args[0].lower() not in ("on", "off")):
        return await rich.reply(message, Doc("📊", "Group Activity").status("info", "Usage", "/activity on or /activity off. Members can use /activityprivacy off to opt out and erase their counts."))
    try:
        if args:
            enabled = args[0].lower() == "on"
            await vdb.db[_SETTINGS].update_one(
                {"_id": _group_key(bot_id, chat_id)},
                {"$set": {"bot_id": bot_id, "chat_id": chat_id, "enabled": enabled}}, upsert=True,
            )
        else:
            enabled = await _enabled(bot_id, chat_id)
    except Exception:
        return await _error(message)
    detail = ("Counts start only for messages this bot receives after enablement. Members can use /activityprivacy off to opt out and erase their counts. Rankings are visible in the group."
              if enabled else "Counting is off. Existing totals are retained; each member can use /activityprivacy off to erase their counts.")
    return await rich.reply(message, Doc("📊", "Group Activity").status("info", "Counting on" if enabled else "Counting off", detail))


@Client.on_message(filters.group & filters.incoming & ~filters.service, group=7)
async def activity_counter(client: Client, message: Message):
    if not plugin_enabled("group_activity"):
        return
    scope = _scope(client, message)
    user = getattr(message, "from_user", None)
    uid = getattr(user, "id", None)
    if not scope or type(uid) is not int or uid <= 0 or getattr(user, "is_bot", None) is not False or getattr(message, "sender_chat", None):
        return
    # Commands are intentionally excluded, including commands in media captions.
    content = getattr(message, "text", None) or getattr(message, "caption", None) or ""
    if content.startswith("/"):
        return
    bot_id, chat_id = scope
    try:
        if not await _enabled(bot_id, chat_id):
            return
        # All counters and the privacy flag share one atomic Mongo document.
        await _count_message(bot_id, chat_id, user, _utc_day())
    except Exception:
        log.warning("Group activity counter update failed")


@Client.on_message(filters.group & filters.command("activityprivacy") & PLG_FILTER)
async def activityprivacy_cmd(client: Client, message: Message):
    scope = _scope(client, message)
    user = message.from_user
    uid = getattr(user, "id", None)
    if not scope or type(uid) is not int or uid <= 0:
        return await rich.reply(message, Doc("🔒", "Activity Privacy").status("warning", "Identity unavailable", "Use this command as yourself, not anonymously."))
    bot_id, chat_id = scope
    args = (message.command or [])[1:]
    if args and (len(args) != 1 or args[0].lower() not in ("on", "off")):
        return await rich.reply(message, Doc("🔒", "Activity Privacy").status("info", "Usage", "/activityprivacy off removes your counts and opts out. /activityprivacy on opts back in."))
    col = vdb.db[_MEMBERS]
    key = _member_key(bot_id, chat_id, uid)
    try:
        if args:
            opted_out = args[0].lower() == "off"
            update = {"$setOnInsert": {"bot_id": bot_id, "chat_id": chat_id, "user_id": uid},
                      "$set": {"opted_out": opted_out}}
            if opted_out:
                update["$unset"] = {"messages": "", "last_active": "", "display_name": "", "daily": ""}
            await col.update_one({"_id": key}, update, upsert=True)
        else:
            doc = await col.find_one({"_id": key})
            opted_out = bool(doc and doc.get("opted_out"))
    except Exception:
        return await _error(message)
    detail = ("Your counts and stored name were removed. An opt-out marker remains for this bot and group."
              if opted_out else "You are eligible for counting only while this group has activity enabled. Use /activityprivacy off to opt out and erase your counts.")
    return await rich.reply(message, Doc("🔒", "Activity Privacy").status("info", "Opted out" if opted_out else "Eligible for counting", detail))


_PAGE_SIZE = 10
_MAX_PAGE = 100
_PERIODS = {"all": "All recorded time", "today": "Today (UTC)", "week": "Last 7 UTC dates, including today"}


async def _leaderboard(client, message, period: str, page: int):
    """Build one bounded, group-scoped page; no global count query required."""
    bot_id, chat_id = _scope(client, message)
    day = _utc_day()
    enabled = await _enabled(bot_id, chat_id)
    col = vdb.db[_MEMBERS]
    offset = (page - 1) * _PAGE_SIZE
    if period == "all":
        rows = await (col.find(_query(bot_id, chat_id))
                      .sort([("messages", -1), ("user_id", 1)])
                      .skip(offset).limit(_PAGE_SIZE + 1).to_list(_PAGE_SIZE + 1))
        count_key = "messages"
    else:
        cursor = col.aggregate([
            {"$match": _query(bot_id, chat_id)},
            {"$addFields": {"period_count": _period_expression(day, 1 if period == "today" else 7)}},
            {"$match": {"period_count": {"$gt": 0}}},
            {"$sort": {"period_count": -1, "user_id": 1}},
            {"$skip": offset},
            {"$limit": _PAGE_SIZE + 1},
        ])
        rows = await cursor.to_list(_PAGE_SIZE + 1)
        count_key = "period_count"
    has_next = len(rows) > _PAGE_SIZE and page < _MAX_PAGE
    rows = rows[:_PAGE_SIZE]
    doc = Doc("🏆", "Top Members", _PERIODS[period] + f" · Page {page} · this group and bot")
    if rows:
        doc.table([(str(offset + i), str(row.get("display_name") or "Member")[:64], str(row.get(count_key, 0)))
                   for i, row in enumerate(rows, 1)], header=("Rank", "Member", "Messages"))
    else:
        doc.status("empty", "No recorded activity", "No messages were recorded for this page and period.")
    detail = ("Counting is on. " if enabled else "Counting is off; showing retained totals. ")
    detail += "Use /activityprivacy off to opt out. Only received messages are counted."
    if period != "all":
        detail += " Period tracking is not retroactive."
    doc.status("info", "Privacy & accuracy", detail)
    tabs = [styled_button(label, callback_data=f"plg_act:board:{value}:1", style="success" if period == value else "primary")
            for value, label in (("all", "All time"), ("today", "Today"), ("week", "7 days"))]
    nav = []
    if page > 1:
        nav.append(styled_button("← Previous", callback_data=f"plg_act:board:{period}:{page - 1}"))
    if has_next:
        nav.append(styled_button("Next →", callback_data=f"plg_act:board:{period}:{page + 1}"))
    return doc, InlineKeyboardMarkup([tabs] + ([nav] if nav else []))


def _leaderboard_args(args):
    period, page = "all", 1
    if len(args) > 2:
        return None
    if args:
        if args[0].lower() in _PERIODS:
            period = args[0].lower()
            args = args[1:]
        elif len(args) != 1:
            return None
        if args:
            raw = args[0]
            if not raw.isascii() or not raw.isdigit() or len(raw) > 3:
                return None
            page = int(raw)
    if not 1 <= page <= _MAX_PAGE:
        return None
    return period, page


@Client.on_message(filters.group & filters.command("topmembers") & PLG_FILTER)
async def topmembers_cmd(client: Client, message: Message):
    if not _scope(client, message):
        return await _error(message)
    selected = _leaderboard_args((message.command or [])[1:])
    if selected is None:
        return await rich.reply(message, Doc("🏆", "Top Members").status("info", "Usage", "/topmembers [all|today|week] [page 1–100]. Time periods use UTC dates."))
    try:
        doc, markup = await _leaderboard(client, message, *selected)
    except Exception:
        return await _error(message)
    # Leaderboards are group-visible and use a normal message so pagination
    # does not depend on ephemeral-message edit support.
    return await message.reply_text(doc.classic(), reply_markup=markup, parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^plg_act:board:[A-Za-z0-9_]+:[0-9]+$"))
async def leaderboard_callback(client: Client, query: CallbackQuery):
    if not plugin_enabled("group_activity"):
        return await query.answer("Group activity is disabled.", show_alert=True)
    message = query.message
    if (not message or not query.from_user or not message.chat
            or message.chat.type not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP)
            or not _scope(client, message)):
        return await query.answer("Open this leaderboard in its group.", show_alert=True)
    parts = (query.data or "").split(":")
    if len(parts) != 4 or parts[0:2] != ["plg_act", "board"]:
        return await query.answer("Invalid leaderboard action.", show_alert=True)
    selected = _leaderboard_args(parts[2:])
    if selected is None:
        return await query.answer("Invalid leaderboard page.", show_alert=True)
    try:
        doc, markup = await _leaderboard(client, message, *selected)
    except Exception:
        return await query.answer("Leaderboard unavailable. Try again later.", show_alert=True)
    try:
        await message.edit_text(doc.classic(), reply_markup=markup, parse_mode=enums.ParseMode.HTML)
    except MessageNotModified:
        return await query.answer()
    except Exception:
        return await query.answer("Could not update leaderboard. Try /topmembers again.", show_alert=True)
    return await query.answer()


async def _member_stats(message: Message, scope, user_id: int):
    bot_id, chat_id = scope
    try:
        col = vdb.db[_MEMBERS]
        record = await col.find_one({"_id": _member_key(bot_id, chat_id, user_id), "opted_out": {"$ne": True}, "messages": {"$gt": 0}})
        enabled = await _enabled(bot_id, chat_id)
        if record:
            count = record["messages"]
            query = _query(bot_id, chat_id)
            query["$or"] = [{"messages": {"$gt": count}}, {"messages": count, "user_id": {"$lt": user_id}}]
            rank = 1 + await col.count_documents(query)
    except Exception:
        return await _error(message)
    doc = Doc("📈", "Member Activity", "Recorded messages · this group and bot")
    if not record:
        doc.status("empty", "No visible activity", "No recorded count is available for this member.")
    else:
        today, week = _period_totals(record, _utc_day())
        doc.kv_table([("Member", str(record.get("display_name") or "Member")[:64]),
                      ("All-time messages", str(count)), ("All-time rank", f"#{rank}"),
                      ("Today (UTC)", str(today)), ("Last 7 UTC dates", str(week))])
        doc.status("info", "Period totals", "Period tracking starts with the upgrade; older totals are not backfilled.")
    doc.status("info", "Counting", "On" if enabled else "Off; retained totals shown")
    return await rich.reply(message, doc)


@Client.on_message(filters.group & filters.command("myactivity") & PLG_FILTER)
async def myactivity_cmd(client: Client, message: Message):
    scope = _scope(client, message)
    uid = getattr(message.from_user, "id", None)
    if not scope or type(uid) is not int or uid <= 0:
        return await rich.reply(message, Doc("📈", "Member Activity").status("warning", "Identity unavailable", "Use this command as yourself."))
    return await _member_stats(message, scope, uid)


@Client.on_message(filters.group & filters.command("memberactivity") & PLG_FILTER)
async def memberactivity_cmd(client: Client, message: Message):
    scope = _scope(client, message)
    target = getattr(getattr(message, "reply_to_message", None), "from_user", None)
    uid = getattr(target, "id", None)
    if not scope or type(uid) is not int or uid <= 0:
        return await rich.reply(message, Doc("📈", "Member Activity").status("info", "Usage", "Reply to a member with /memberactivity."))
    return await _member_stats(message, scope, uid)


@Client.on_message(filters.group & filters.command("groupactivity") & PLG_FILTER)
async def groupactivity_cmd(client: Client, message: Message):
    scope = _scope(client, message)
    if not scope:
        return await _error(message)
    bot_id, chat_id = scope
    try:
        enabled = await _enabled(bot_id, chat_id)
        day = _utc_day()
        cursor = vdb.db[_MEMBERS].aggregate([
            {"$match": _query(bot_id, chat_id)},
            {"$group": {"_id": None, "members": {"$sum": 1}, "messages": {"$sum": "$messages"},
                        "today": {"$sum": _period_expression(day, 1)},
                        "week": {"$sum": _period_expression(day, 7)}}},
        ])
        rows = await cursor.to_list(length=1)
    except Exception:
        return await _error(message)
    totals = rows[0] if rows else {"members": 0, "messages": 0, "today": 0, "week": 0}
    doc = Doc("📊", "Group Activity", "Recorded messages · this group and bot").kv_table([
        ("Members (all time)", str(totals["members"])), ("Messages (all time)", str(totals["messages"])),
        ("Today (UTC)", str(totals["today"])), ("Last 7 UTC dates", str(totals["week"])),
        ("Counting", "On" if enabled else "Off; retained totals shown"),
    ])
    doc.status("info", "Scope", "Only received non-command messages are counted. Opted-out members are excluded; period totals start with the upgrade.")
    return await rich.reply(message, doc)
