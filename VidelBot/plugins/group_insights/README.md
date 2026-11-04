# Group Insights

A built-in, read-only plugin for diagnosing existing `group_mod` behavior. Both commands run in groups and require an administrator with Telegram's restrict-members right (or an anonymous group administrator). Group results are visible in the chat; use sample text that is safe to share. The plugin itself does not persist samples or results, although Telegram and other components may retain messages.

## Policy simulator

- `/policysim sample text` evaluates the current locks and keyword filters for a hypothetical non-admin.
- Reply to an existing message with `/policysim` to include supported forward, media and via-bot metadata.
- `/policysim --locks=links,media sample text` evaluates draft locks **without changing settings**. The same option works when replying to a message. Use `--locks=none` to test no locks; choices are `links`, `forwards`, `bots`, `media`, and `all`.

The simulator uses the same content-lock and keyword matching code as the live passive guard. A lock takes priority over a keyword reply for a non-admin. Draft mode changes only the simulated locks; it still uses the group's current keyword filters. It does not send the configured filter response or echo the sample text. It does not simulate anti-flood, raids, join requests, an actual member's admin status, permissions, or Telegram action success. The existing group settings cache can lag across workers. The parent `group_mod` plugin must be enabled.

## Permission health

`/ghealth` fetches the bot's current Telegram membership and shows available delete, restrict, invite, change-info, pin and topic rights. Unavailable fields and failed lookups are shown as unknown, never as granted. This snapshot does not guarantee that a particular action will succeed. The Insights plugin can be enabled or disabled through the existing plugin manager.

## Boundaries

The plugin reads existing group settings keyed by chat ID; it adds no separate storage. It inherits the current group-mod configuration model, including its shared-chat/bot isolation and cache limitations, which need a wider architecture review. No live Telegram or multi-process MongoDB integration has been verified for this stage.
