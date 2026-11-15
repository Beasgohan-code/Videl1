# TelegramDL reference and Content Saver increment

The requested reference was cloned from [Shineii86/TelegramDL](https://github.com/Shineii86/TelegramDL) at `54d228afc917384b4b7d3c699ae7dedf63420067`. Its [license](https://github.com/Shineii86/TelegramDL/blob/54d228afc917384b4b7d3c699ae7dedf63420067/LICENSE) is MIT, copyright 2024–2026 Shinei Nouzen (Shineii86). This increment independently implements ideas from its link/batch handling; no TelegramDL source files or dependencies are vendored. Any future copying must preserve the applicable copyright and permission notices and review dependency licenses.

## What was integrated

Videl's existing Content Saver now recognizes explicit public post and range links, public web-preview (`/s/<name>/<post>`) links, private-channel (`/c/<id>/<post>`) links and its existing bot-link form (`/b/<name>/<post>`). It validates the entire path and the message range, allows the `?single` query and rejects ambiguous story, invite, topic/comment, fragment, malformed and unknown query forms instead of silently treating part of one as a different post. It does not join chats or follow web links. Source/destination access and content protections remain governed by Telegram and the existing clients; the parser does not grant access.

An in-process per-user claim prevents a second saver request from starting while the first is still running or cleaning up, including immediately after cancellation. It does not coordinate separate workers/bots, resolve old callback buttons, or make the transfer durable. Videl keeps the existing WzGram receiver and temporary `no_updates` personal client; it does not install TelegramDL's ftmgram or add a competing update receiver.

## Why the rest was not imported

TelegramDL also provides user-session/client flows, automatic chat joins, download/upload processing, batch progress and local JSON checkpoints, and yt-dlp. Videl already has related saver, queue and session functions. Blindly combining clients, shared/global checkpoint files, session handling, automatic joins or network downloaders could create credential exposure, tenant isolation, conflicting receiver, duplicate-delivery or SSRF risks. These features need separate threat modeling, explicit product decisions and real-service testing; this review is not an exhaustive audit of either project.

## Limitations

The saver still falls back to a user session after a bot-copy exception, its batch limits and quota behavior are unchanged, and progress/cancellation are process-local. An accepted link is not evidence a message can be copied or saved. Story, topic/comment and invite links are intentionally not implemented. Existing login/session protections depend on deployment configuration. No production deployment or real Telegram integration has been verified by the offline tests.
