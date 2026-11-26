"""Opt-in, per-bot decorative button icons with readable original labels.

The small emoji-ID palette is based on IDs listed by the Videl0 reference project;
Telegram availability and bot eligibility are not inferred or verified here.
"""
import config

# Telegram identifiers are public references, not a guarantee the bot can use them.
# Match only an existing leading symbol; do not interpret button text as markup.
_PALETTE = (
    ("✅", "5084979757905347540"),
    ("❌", "5210952531676504517"),
    ("⚠", "5447644880824181073"),
    ("🔒", "5296369303661067030"),
    ("🔓", "6037496202990194718"),
    ("🛡", "6030445631921721471"),
    ("🎵", "5463107823946717464"),
    ("🎶", "6037364759811068375"),
    ("📌", "5397782960512444700"),
    ("➡", "5416117059207572332"),
    ("⬅", "5960671702059848143"),
    ("🔙", "5960671702059848143"),
    ("📊", "5231200819986047254"),
    ("🔗", "5271604874419647061"),
    ("🔍", "5231012545799666522"),
    ("⚙", "5904258298764334001"),
    ("✨", "5325547803936572038"),
    ("🤖", "6030400221232501136"),
    ("📁", "6037475557082403885"),
    ("📄", "6034969813032374911"),
    ("🗑", "6039522349517115015"),
    ("➕", "6032924188828767321"),
    ("🎮", "5116175844837950263"),
    ("💎", "5427168083074628963"),
    ("⭐", "5438496463044752972"),
    ("👑", "5217822164362739968"),
)


def allowed_bot(bot_id):
    """Unknown clients and bots not explicitly allowlisted always stay plain."""
    return (config.PREMIUM_BUTTON_ICONS and type(bot_id) is int and bot_id > 0
            and bot_id in config.PREMIUM_BUTTON_ICON_BOT_IDS)


def icon_for(button, bot_id):
    """Only decorate simple callbacks/URLs; keep all action fields and label intact."""
    if not allowed_bot(bot_id) or getattr(button, "icon_custom_emoji_id", None):
        return None
    if (getattr(button, "disabled", None) is not None or getattr(button, "pay", False)
            or getattr(button, "login_url", None) is not None or getattr(button, "callback_game", None) is not None):
        return None
    callback = getattr(button, "callback_data", None)
    url = getattr(button, "url", None)
    if callback is None and not url:
        return None
    if isinstance(callback, bytes):
        try:
            callback = callback.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(callback, str) and (callback == "noop" or callback.startswith("noop:")):
        return None
    text = getattr(button, "text", "")
    if not isinstance(text, str):
        return None
    label = text.lstrip()
    for glyph, identifier in _PALETTE:
        if label.startswith(glyph):
            # Hard bounds, despite the built-in IDs, to avoid malformed API data.
            if identifier.isascii() and identifier.isdigit() and 0 < int(identifier) < 2**63:
                return identifier
    return None


