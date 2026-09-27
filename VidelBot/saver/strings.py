"""Texts for the Content Saver module."""
from config import FREE_LIMIT_DAILY

HELP_TXT = f"""<b>📥 Content Saver — Guide</b>

<blockquote expandable>
<b>1. Public channels</b> (no login)
• Just send the post link, e.g. <code>https://t.me/channel/123</code>

<b>2. Private / restricted channels</b>
• Use <code>/login</code> once to connect your account
• Then send links like <code>https://t.me/c/123456789/10</code>

<b>3. Batch</b>
• Send a range: <code>https://t.me/channel/100-120</code>
• Stop anytime with <code>/cancel</code>

<b>4. Encode what you saved</b>
• Reply <code>/dl</code> to any saved video to compress / re-encode it
</blockquote>

<b>⚙️ Customise</b>
<blockquote>/set_caption · /see_caption · /del_caption
/set_thumb · /view_thumb · /del_thumb · /thumb_mode
/set_del_word · /rem_del_word · /set_repl_word · /rem_repl_word
/setchat &lt;chat_id&gt; — auto-forward saved files</blockquote>

<b>💎 Plans</b>
<blockquote>Free: {FREE_LIMIT_DAILY} saves / 24h · 5 posts per batch
Premium: unlimited — /myplan · /premium</blockquote>
"""

COMMANDS_TXT = """<b>📜 Content Saver Commands</b>

<b>👤 Main</b>
<blockquote>/login — Connect account
/logout — Disconnect account
/cancel — Stop current task
/myplan — Plan & quota
/premium — Upgrade options</blockquote>

<b>📤 Dump Chat</b>
<blockquote>/setchat &lt;chat_id&gt; — Forward destination
/setchat clear — Remove dump chat</blockquote>

<b>✍️ Caption & Words</b>
<blockquote>/set_caption &lt;text&gt; — {filename} {size}
/see_caption · /del_caption
/set_del_word w1 w2 · /rem_del_word w1
/set_repl_word old new · /rem_repl_word old</blockquote>

<b>🖼 Thumbnail</b>
<blockquote>/set_thumb (reply to photo) · /view_thumb
/del_thumb · /thumb_mode</blockquote>
"""
