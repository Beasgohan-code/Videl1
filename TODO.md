# Videl1 — Upgrade Roadmap & Status (`TODO.md`)

> Active codebase: **`VidelBot/`** (deployed via the single root **`Dockerfile`** outside `VidelBot/`).
> Legacy files in repository root (`modules/`, `helper/`, `info/`) are retained untouched as historical prototype artifacts and are excluded from Docker images via root-anchored rules in `.dockerignore`.

---

## Completed Milestones

### Phase 1 — Repository, Dependency & API Audit
- [x] Audited `VidelBot/` runtime, MongoDB schemas, 21 existing test suites, and deploy manifests.
- [x] Consolidated Docker deployment to a single root **`Dockerfile`** and **`.dockerignore`** outside `VidelBot/` (removing the duplicate `VidelBot/Dockerfile` and `VidelBot/.dockerignore`) with root-anchored `.dockerignore` rules and build-time `compileall` + runtime-stack verification.
- [x] Verified compatibility between **`wzgram==3.1.6`** (MTProto Layer 232 + `WarpCrypto==2.1.0`), **`richgram==0.2.0`**, and **`aiogram==3.31.0`** (Bot API 10.3).
- [x] Audited `SilentDemonSD/WZML-X` (`wzv3-dev`) plugin manifests, installer safeguards, and reference plugins.

### Phase 2 — Centralized Richgram Presentation & Bot API 10.3 Bridge
- [x] Upgraded `VidelBot/core/rich.py` into a 3-tier centralized Richgram presentation layer:
  1. Bot API 10.3 `sendRichMessage` / `editMessageText(rich_message=…)` (`aiogram` bridge),
  2. Native WzGram Layer 232 `send_rich_message(InputRichMessage)` / `edit_message_text(rich_text=…)` (including clone workers),
  3. Classic FileStore HTML fallback (`◈` small-caps rows + `<blockquote expandable>`).
- [x] Added `rich_send`, `rich_reply`, `rich_edit`, `Doc.kv_table`, `Doc.pull_quote`, `Doc.pre`, `Doc.math`, `Doc.thinking`, `Doc.buttons_row`, `Doc.slideshow` / `Doc.collage`, `Doc.status` (`rich_status`), `Doc.progress` (`rich_progress`), safe numeric `custom_emoji` (`Doc.emoji`), ordered/unordered `Doc.items`, `Doc.plain`, and safe HTML-balanced length clamping (`clamp_html`).
- [x] Added full Ephemeral Message lifecycle (`group_reply`, `ephemeral_edit`, `ephemeral_delete`, `answer_ephemeral`) across Bot API 10.3, WzGram Layer 232, and standard message fallback, plus topic thread preservation (`thread_id_of`).
- [x] Upgraded `VidelBot/core/stream.py` with stoppable rich drafts (`send_rich_message_draft(..., can_stop=True)`) and `MessageGenerationStopped` cancellation.
- [x] Added short `RetryAfter` single-retry handling and 60s network outage circuit-breaker in `VidelBot/core/botapi.py`.

### Phase 3 — WZML-X Modular Plugin System & Staged Owner Approval
- [x] Implemented `PluginManifest`, `PluginBase`, and `PluginManager` (`VidelBot/core/plugin_manager.py`) supporting both `plugin.yaml` and `wzml_plugin.yml` manifests, WZML-X field aliases, semver bounds, `importlib.metadata` dependency verification, command collision checks, `config_schema` validation (`on_configure`), MongoDB persistence, and clean `unload_all` shutdown hooks.
- [x] Implemented safe archive extraction + **two-step Staged Security Review & Explicit Owner Approval** (`VidelBot/core/plugin_installer.py`, `VidelBot/core/plugins_cmd.py`):
  - Blocks `..` path traversal, absolute paths, symlinks, hardlinks, and oversized archives.
  - Parses all `.py` files with `ast.parse` (zero code execution during staging), computes SHA-256 digests, flags sensitive imports/calls (`subprocess`, `eval/exec`, `ctypes`, credential attributes), and requires explicit `config.OWNERS` confirmation (`plg_stg:ok:<id>`) before installation.
- [x] Bundled 5 modular YAML plugins under `VidelBot/plugins/`:
  - `imdb` (`/imdb`), `anilist` (`/anime`, `/manga`, `/character`), `mydramalist` (`/mdl`), `exportsession` (`/exportsession`), and `group_mod` (`/gsettings` & moderation suite).
- [x] Upgraded `/mediainfo` with SSRF-guarded direct HTTP `Range` probing and `/speedtest` with Richgram cards + IPv4/IPv6 masking.

### Phase 4 — Group Management & Bot API 10.3 Capabilities
- [x] Implemented `VidelBot/plugins/group_mod/` with live Telegram `get_chat_member` permission verification for both caller and bot on every admin operation, anonymous group admin pass-through (`core/middleware.py`), and admin-target immunity.
- [x] Integrated Bot API 10.3 / WzGram Layer 232 features:
  - Member tags (`/tag`, `/deltag` via `setChatMemberTag`),
  - Reaction moderation (`/clearreactions` & `/clearreactions all` via `deleteMessageReaction` / `deleteAllMessageReactions`),
  - Forum topic management (`/topic new|close|reopen`),
  - Join-request workflows (`/joinmode off|notify|auto`),
  - Managed clone bot push onboarding (`@Client.on_managed_bot()`) + token rotation + `BotAccessSettings` helpers,
  - Interactive & Business checklists (`/checklist`) and upgraded collectible gift inspection (`/nftgift`).

### Phase 5 — Codebase Audit, Security Hardening, CI & Reliability (Stages A–E)
- [x] **Atomic Stars Payment Idempotency & Validation:** Added atomic `charge_id` claiming (`_claim_payment` / `CloneExtras.claim_payment`), unique sparse `charge_id` indexes, and post-checkout currency/amount/payer verification across both main-bot (`core/payments.py`) and clone-worker (`filestore/worker_bot/extras.py`) Stars flows.
- [x] **SSRF, Subprocess & HTML Escaping Hardening:** Added DNS/IP public-endpoint SSRF validation (`validate_public_endpoint`), redirect blocking, and scheme validation in `filestore/utils/shortener.py`; replaced shell execution in `/update` (`core/admin.py`) with `create_subprocess_exec` + credential redaction + `html.escape`; escaped untrusted names/errors in `/admins`, `watchdog`, and clone `/id`; and added orphan clone-worker disconnect cleanup when `after_start` fails.
- [x] **MongoDB Pool Sharing, Task Drain & Backup Dry-Run:** Shared the singleton `AsyncIOMotorClient` pool across `VidelDB`, `saver`, `VideoEncoder`, and `filestore`; made `VideoEncoder` `add_user` atomic (`$setOnInsert`); added `core.bg.cancel_all()` + `watchdog.stop()` shutdown draining; and upgraded `core/backup.py` with 256 MiB decompression caps, schema validation, interactive `🔍 Dry-Run` (`bkr:dry`), and post-restore cache reload.
- [x] **Plugin Health Diagnostics, Scheduler GC Guard, Safe OAuth JSON, CI & Regression Tests:** Added `register_handler_once()` duplicate-registration protection, `plugin_manager.health_report()` / `load_errors` diagnostics in `/plugins`, `weakref`-guarded `id(message)` collision eviction in `VideoEncoder/utils/scheduler.py`, safe JSON OAuth storage replacing `pickle.load` in `VideoEncoder/utils/uploads/drive/__init__.py`, `.github/workflows/ci.yml`, and `tests/test_stage_b_d_regressions.py` (`733 passed, 33 skipped, 0 failed`).

---

## Remaining Platform Constraints & Future Follow-Ups
- **Live Telegram E2E Validation**: All unit/regression tests run offline against simulated MTProto & Bot API sessions; end-to-end verification of `sendRichMessage`, `setChatMemberTag`, `deleteAllMessageReactions`, `sendChecklist` (Business), and `ManagedBotUpdated` requires a live Telegram bot token with appropriate BotFather flags (`Bot Management Mode`) and supergroup administrator privileges.
- **Process-Level Plugin Isolation**: Approved plugins run in-process with the bot (matching WZML-X's design); only bot owners (`OWNER_ID` / `OWNERS`) can approve staged plugins.
- **Telegram Command Scope Cap**: Telegram caps each `BotCommandScope` at 100 commands (`USER + ADMIN + OWNER = 100/100`; `GROUP_COMMANDS = 45/100`). Adding future private-scope commands requires replacing or consolidating existing menu entries.
