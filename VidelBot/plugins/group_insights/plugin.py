"""Read-only diagnostics for the existing group_mod plugin.

No message contents, simulation results, or health results are stored.
"""
from __future__ import annotations

from types import SimpleNamespace

from pyrogram import Client, enums, filters
from pyrogram.types import Message

from core import rich
from core.plugin_manager import PluginBase, plugin_enabled, plugin_filter
from core.rich import Doc
from plugins.group_mod import plugin as gmod

PLG_FILTER = plugin_filter("group_insights")


class Plugin(PluginBase):
    pass


async def _authorize(client: Client, message: Message) -> tuple[bool, str]:
    uid = message.from_user.id if message.from_user else None
    return await gmod.verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_restrict_members",
        is_anon_admin=gmod._is_anon_group_admin(message), check_bot=False,
    )


@Client.on_message(filters.group & filters.command("policysim") & PLG_FILTER)
async def policysim_cmd(client: Client, message: Message):
    """Preview only the rules implemented in group_mod's passive guard."""
    ok, err = await _authorize(client, message)
    if not ok:
        return await rich.reply(message, Doc("🔒", "Policy Simulator").status("error", "Permission check failed", err))
    if not plugin_enabled("group_mod"):
        return await rich.reply(message, Doc("🧪", "Policy Simulator").status("warning", "Group moderation disabled", "Enable group_mod to preview its active rules."))
    raw = (message.text or "").split(maxsplit=1)
    sample_text = raw[1].strip() if len(raw) > 1 else ""
    draft_locks = None
    if sample_text.lower().startswith("--locks="):
        option = sample_text.split(maxsplit=1)
        names = option[0][len("--locks="):].lower().split(",")
        if names == ["none"]:
            draft_locks = []
        elif not names or any(name not in gmod.VALID_LOCKS for name in names):
            return await rich.reply(message, Doc("🧪", "Policy Simulator").status("warning", "Invalid draft locks", "Use --locks=links,forwards,bots,media,all or --locks=none. No settings are changed."))
        else:
            draft_locks = list(dict.fromkeys(names))
        sample_text = option[1].strip() if len(option) > 1 else ""
    sample = message.reply_to_message
    if sample is not None and sample_text:
        return await rich.reply(message, Doc("🧪", "Policy Simulator").status("info", "Choose one sample", "Reply to a message with /policysim, or use /policysim <sample text>."))
    if sample is None:
        if not sample_text:
            return await rich.reply(message, Doc("🧪", "Policy Simulator").status("info", "Usage", "Reply to a message or use /policysim <sample text>. Add --locks=links,media (or --locks=none) to test draft locks. Results appear in this group."))
        sample = SimpleNamespace(text=sample_text, caption=None)
    cfg = await gmod.get_group_cfg(message.chat.id)
    locks = draft_locks if draft_locks is not None else (cfg.get("locks") or [])
    fmap = cfg.get("filters") or {}
    text = getattr(sample, "text", None) or getattr(sample, "caption", None) or ""
    hit = gmod.match_locked_content(sample, locks)
    # A lock takes precedence over keyword replies in the real guard for non-admins.
    keyword = None if hit else gmod.match_keyword_filter(text, fmap)
    if hit:
        result = f"Lock matched: {hit}. The guard would attempt to delete a non-admin's message."
    elif keyword:
        result = "A keyword filter matched. The guard would attempt to send its configured reply."
    else:
        result = "No supported content lock or keyword filter matched."
    doc = Doc("🧪", "Policy Simulator", "Read-only preview")
    doc.status("info", "Mode", "Draft locks; current keyword filters" if draft_locks is not None else "Current locks and keyword filters")
    doc.status("info", "Result", result)
    doc.status("warning", "Limits", "Hypothetical non-admin only. Flood, raid, join rules and permissions are not simulated. Telegram action success is not guaranteed; settings may be cached.")
    return await rich.reply(message, doc)


_RIGHTS = (
    ("Delete messages", "can_delete_messages"),
    ("Restrict members", "can_restrict_members"),
    ("Invite / approve joins", "can_invite_users"),
    ("Change group info", "can_change_info"),
    ("Pin messages", "can_pin_messages"),
    ("Manage topics", "can_manage_topics"),
)


@Client.on_message(filters.group & filters.command("ghealth") & PLG_FILTER)
async def ghealth_cmd(client: Client, message: Message):
    """Query current Telegram membership; do not infer missing rights as granted."""
    ok, err = await _authorize(client, message)
    if not ok:
        return await rich.reply(message, Doc("🔒", "Group Health").status("error", "Permission check failed", err))
    bot_id = getattr(getattr(client, "me", None), "id", None)
    if not bot_id:
        return await rich.reply(message, Doc("🩺", "Group Health").status("warning", "Bot identity unavailable", "Cannot verify bot permissions right now."))
    try:
        member = await client.get_chat_member(message.chat.id, bot_id)
    except Exception:
        return await rich.reply(message, Doc("🩺", "Group Health").status("warning", "Telegram lookup failed", "Cannot verify bot permissions right now."))
    status = getattr(member, "status", None)
    if status == enums.ChatMemberStatus.OWNER:
        rows = [(label, "Granted (owner)") for label, _ in _RIGHTS]
    elif status == enums.ChatMemberStatus.ADMINISTRATOR:
        priv = getattr(member, "privileges", None)
        rows = []
        for label, attr in _RIGHTS:
            value = getattr(priv, attr, None) if priv is not None else None
            rows.append((label, "Unknown" if value is None else "Granted" if value else "Not granted"))
    elif status in (enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.RESTRICTED,
                    enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED):
        rows = [("Bot status", "Not an administrator")]
    else:
        rows = [("Bot status", "Unknown")]
    doc = Doc("🩺", "Group Health", "Live Telegram permission lookup").kv_table(rows)
    doc.status("info", "Note", "This is a snapshot, not a guarantee that an action will succeed. Feature-specific requirements can differ.")
    return await rich.reply(message, doc)
