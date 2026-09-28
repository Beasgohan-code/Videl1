"""Texts for the Content Saver module (Videl design system – see core/design.py)."""
from config import FREE_LIMIT_DAILY
from core.design import DOT, bullets, cmd, details, footer, heading, page, section

HELP_TXT = page(
    heading("📥", "Content Saver", "Save posts from any channel — even restricted ones."),
    section("🚀", "How it works", bullets(
        "<b>Public channels</b> — just send the post link, e.g. <code>https://t.me/channel/123</code>",
        "<b>Private channels</b> — " + cmd("login") + " once, then send <code>https://t.me/c/123456789/10</code>",
        "<b>Batch</b> — send a range <code>https://t.me/channel/100-120</code>" + DOT + "stop with " + cmd("cancel"),
        "<b>Encode it</b> — reply " + cmd("dl") + " to any saved video")),
    details("⚙️", "Customise",
            cmd("set_caption") + DOT + cmd("see_caption") + DOT + cmd("del_caption"),
            cmd("set_thumb") + DOT + cmd("view_thumb") + DOT + cmd("del_thumb") + DOT + cmd("thumb_mode"),
            cmd("set_del_word") + DOT + cmd("rem_del_word") + DOT + cmd("set_repl_word") + DOT + cmd("rem_repl_word"),
            "<code>/setchat &lt;chat_id&gt;</code> — auto-forward saved files"),
    section("💎", "Plans",
            f"<b>Free</b>{DOT}{FREE_LIMIT_DAILY} saves / 24h{DOT}5 posts per batch",
            f"<b>Premium</b>{DOT}unlimited — {cmd('myplan')}{DOT}{cmd('premium')}"),
)

COMMANDS_TXT = page(
    heading("📜", "Content Saver Commands", "Everything the saver understands."),
    section("👤", "Main", bullets(
        cmd("login", "connect account") + DOT + cmd("logout", "disconnect"),
        cmd("cancel", "stop current task"),
        cmd("myplan", "plan &amp; quota") + DOT + cmd("premium", "upgrade options"))),
    details("💎", "Premium &amp; rewards", bullets(
        cmd("buy", "pay with ⭐ Stars") + DOT + cmd("mysub", "monthly auto-renew"),
        cmd("gift", "Premium for a friend"),
        cmd("trial", "free trial") + DOT + "<code>/redeem CODE</code>",
        cmd("refer", "invite friends &amp; earn Premium") + DOT + cmd("support"))),
    details("📤", "Dump chat", bullets(
        "<code>/setchat &lt;chat_id&gt;</code> — forward destination",
        "<code>/setchat clear</code> — remove dump chat")),
    details("✍️", "Caption &amp; words", bullets(
        "<code>/set_caption &lt;text&gt;</code> — use <code>{filename}</code> <code>{size}</code>",
        cmd("see_caption") + DOT + cmd("del_caption"),
        "<code>/set_del_word w1 w2</code>" + DOT + "<code>/rem_del_word w1</code>",
        "<code>/set_repl_word old new</code>" + DOT + "<code>/rem_repl_word old</code>")),
    details("🖼", "Thumbnail", bullets(
        cmd("set_thumb", "reply to a photo") + DOT + cmd("view_thumb"),
        cmd("del_thumb") + DOT + cmd("thumb_mode"))),
    footer("Tip: just send a link — no command needed."),
)
