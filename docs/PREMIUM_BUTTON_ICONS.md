# Optional premium button icons

The primary bot's ordinary Bot API text sends and edits can decorate matching
inline callback and URL buttons with custom-emoji icons. This is **off by
default**. Opt in by setting `PREMIUM_BUTTON_ICONS=True` and putting the
primary bot's numeric ID (not its token) in `PREMIUM_BUTTON_ICON_BOT_IDS`.
Restart the process after changing environment variables. To opt out, set the
flag to false or remove the ID. The bridge and coloured-button feature must
also be enabled for these paths to run.

Labels, callback data and URLs are retained unchanged, including the ordinary
Unicode symbol, so they remain readable where the icon is not displayed. This
can result in both an icon and the Unicode symbol appearing. Explicit custom
icons are preserved; disabled/noop, login, payment, copy and other special
buttons are not automatically decorated. Rich messages, ephemeral messages,
MTProto fallback sends, clone workers, message text and arbitrary plugins are
not decorated by this feature. There is no markup injection into user text.

The static ID palette was selected from the public identifiers in
[Beasgohan-code/Videl0](https://github.com/Beasgohan-code/Videl0), revision
`6430b1e5e60e0776a7ab519e05cb98c2ca1a2ebf`, which credits NewsEmoji,
tgiosicons, @rijif and SoLo_HaMiD for its emoji references. IDs can stop
working or have restrictions. Telegram controls bot/destination eligibility;
allowlisting is an operator assertion, not a test or entitlement. Test each bot,
destination and relevant keyboard before using this in production.

On a definite Bot API bad request identifying an invalid/forbidden emoji or
button icon, the ordinary send/edit is retried once without **automatically
added** icons. Existing explicit icons are not removed. Network and unknown
errors are not treated as proof of rejection, so they do not trigger that
retry; existing transport fallback behavior still applies. Not every Telegram
error is identifiable this way. Disable the feature if delivery is affected.
Offline tests do not prove live icon availability or Telegram acceptance.
