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
- [x] **Stage F — Subsystem Deep Audit, DB Integrity Doctor & Green CI:** Fixed revoked-admin `/admin` web session persistence in `keep_alive.py` + restored `core.bg.track()`; enforced `_verify_message_ownership()` across all 7 pending text/media input handlers in `filestore/main_bot/plugins/bot_settings.py` and added `no_updates=True` + `finally: temp_client.stop()` across all temporary verification clients; hardened `saver/start.py` link parsing, `MAX_SAVER_BATCH`, literal backslash replacement in `apply_word_filters()`, and `{filename}` HTML escaping; added `core/db_doctor.py` (`/backup check` + `bkr:dedupe_ok` non-destructive duplicate merge); eliminated all remaining unescaped `str(e)` HTML interpolations across all 139 modules; fixed `.github/workflows/ci.yml` runtime-stack check; and added `tests/test_stage_f_subsystems.py`.
- [x] **Stage G — `/ddl` SSRF Guard, Safe Batch Archive Extraction, Session Encryption-at-Rest, `/exec` Isolation & Interactive Rich `/stats`:** Added `leech.check_public()` SSRF validation to VideoEncoder `/ddl` & `/batch` (`handle_download_url` + `handle_url`); hardened `/batch` archive extraction (`chmod +x VidelBot/extract`, `-y -p""`, `.tbz2` path fix, Zip-Slip/Tar-Slip/symlink precheck, `MAX_BATCH_EXTRACT_BYTES` bomb cap, and post-extraction symlink scrubbing); encrypted `saver.users.session` at rest with Fernet (`ENCRYPTION_KEY`) + `bkr:encrypt_ok` in-place legacy secret upgrade; isolated `/exec` & `/sh` (`pyexec.py`) with `ENABLE_OWNER_EXEC`, local `print` capture, timeout termination, and automatic credential redaction; hardened `core/tools.py` & `core/inline.py` (`/qr`, `/json`, `/short`, `/rename`); upgraded `/stats` with interactive Rich tabs (`📊 Overview`, `⚡ Runtime`, `🩺 Health`); and added `tests/test_stage_g_hardening.py` (`779 passed, 1 skipped, 0 failed`).
- [x] **Stage H — Deep Reference Audit of `SilentDemonSD/WZML-X` (`v3.1.6-x` / `wzv3-dev`) & Selective Videl1 Upgrades:**
  - **Licensing & Attribution Review:** Verified `SilentDemonSD/WZML-X` is licensed under **GNU AGPL-3.0** (`LICENSE`), compatible with `VidelBot/LICENSE-VideoEncoder-AGPL` while preserving `VidelBot/LICENSE-save-restricted-content-bot` (MIT) and `/source` (`SOURCE_URL`) AGPL §13 disclosure. All Stage H upgrades were written cleanly as native Videl1 modules (zero large code blocks copied from WZML-X, keeping MIT/core modules free of external copyright encumbrance).
  - **Verified Already Implemented Before Stage H Edits (Preserved Without Regression):**
    - Safe JSON OAuth token storage (`token.json`, zero `pickle.load`) in `VideoEncoder/utils/uploads/drive/__init__.py`.
    - Atomic `charge_id` payment claiming (`_claim_payment` / `CloneExtras.claim_payment`) and unique sparse `charge_id` MongoDB indexes in `core/payments.py` & `filestore/worker_bot/extras.py`.
    - Post-persistence cache ordering in `core/admins.py` (`add_admin` / `remove_admin`).
    - Root `./Dockerfile` consolidation, synchronized `requirements.txt` == `VidelBot/requirements.txt`, and `client.runtime_stack()` (`engine == "WzGram"`, `rich_available is True`, `botapi_engine == "aiogram"`).
    - Staged AST security scanner + SHA-256 + explicit owner approval (`core/plugin_installer.py`) and restart-proof `weakref`-guarded encoder queue (`VideoEncoder/utils/scheduler.py`).
  - **Existing Subsystems Improved & Hardened:**
    - **Fail-Closed Single-Instance Lease (`VidelBot/core/instance.py`, `VidelBot/run.py`, `VidelBot/config.py`):** Replaced fail-open `"starting without it"` behaviour with `InstanceLeaseError` after bounded exponential retry on MongoDB failures or `SINGLE_INSTANCE_TIMEOUT` expiry; added missed-heartbeat split-brain detection (`max_missed_beats=3`) and clean `handover()` termination on shutdown.
    - **Post-Persistence Cache Invariants (`VidelBot/core/db.py`, `VidelBot/core/plugin_manager.py`):** Moved `vdb._known.add()`, `vdb._banned.add/discard()`, `plugin_manager._enabled`, and `plugin_manager._configs` mutations strictly **after** `await ...update_one(...)` succeeds so failed MongoDB writes never poison in-memory state.
    - **Payment Audit & Zero-Price Clone Guard (`VidelBot/core/payments.py`, `VidelBot/filestore/worker_bot/extras.py`, `VidelBot/.github/workflows/ci.yml`):** Stored `payload` and `provider_charge_id` audit fields on payment records; closed a clone `on_payment` edge case where `premium_stars == 0` (disabled clone premium) matched a zero-amount `xp` payload; strengthened `.github/workflows/ci.yml` runtime assertion with `botapi_engine == "aiogram"`.
    - **Backup Integrity & Per-Collection Isolation (`VidelBot/core/backup.py`, `VidelBot/core/admin.py`):** Added `dump_with_report()`, `verify_backup_file()`, embedded `integrity` (`payload_sha256`, `total_docs`, `collections`), SHA-256 verification before dry-run/restore, per-collection error isolation during dump/restore, and SHA-256 captions on `/backup` exports.
    - **Google Drive Resilience, Cancellation & Security (`VidelBot/VideoEncoder/utils/uploads/drive/`, `VidelBot/VideoEncoder/plugins/upload.py`):** Added headless `DriveNotConfiguredError` + Service Account (`GDRIVE_SA_FILE`) + per-request `httplib2.Http(timeout=GDRIVE_HTTP_TIMEOUT)` + `cache_discovery=False`; fixed `UnboundLocalError` on `ms` (upload failure) and `url` (empty `INDEX_URL`); fixed 0-byte uploads (`resumable=False`), non-blocking chunk threads (eliminating the `while status is None: pass` busy-spin), transient HTTP `(408, 429, 500, 502, 503, 504)` exponential backoff, per-chunk `jobs.is_cancelled()` cancellation, partial-download file cleanup, path-traversal filename sanitization, and credential/system-file blocking (`_is_forbidden_upload_target`) on `/vupload`/`/dupload`/`/gupload`.
    - **Graceful Shutdown & Plugin Lifecycle (`VidelBot/core/plugin_manager.py`, `VidelBot/run.py`):** Added `PluginBase.startup()` / `PluginBase.shutdown()` hooks alongside `on_load()` / `on_unload()`, bounded per-hook execution with `asyncio.wait_for(..., timeout=hook_timeout)`, added `required_env` & `ALLOWED_PERMISSIONS` manifest validation, and isolated plugin failures in `health_report()`.
    - **Bot-Wide Unified Task Telemetry, Pause/Resume/Boost, Memory Backpressure & Responsive `/admin` Console (`VidelBot/core/progress.py`, `VidelBot/VideoEncoder/utils/{jobs,scheduler,display_progress,encoding}.py`, `VidelBot/renamer/engine.py`, `VidelBot/saver/start.py`, `VidelBot/VideoEncoder/plugins/status.py`, `VidelBot/watchdog.py`, `VidelBot/keep_alive.py`):** Unified live task telemetry (`pct`, `current`, `total`, `speed_label`, `eta`, `engine`, `paused`) across **VideoEncoder**, **Auto-Rename** (`FloodWait`/`5xx` upload retry added), and **Content Saver** batches; added POSIX `SIGSTOP`/`SIGCONT` + async-gate **Pause / Resume** (`jobs.pause`/`jobs.resume`, cleaning up `SIGSTOP` before `SIGTERM` on cancel) and admin queue **Boost** (`scheduler.promote_to_front`); added container RAM backpressure (`MEMORY_HIGH_WATERMARK_PCT`, `MEMORY_PAUSE_NEW_TASKS_PCT`) and deleted-message / per-chat `FloodWait` circuit breaking; upgraded `/status` with `All`/`Mine`/`Running` filters, pagination, and inline **Pause / Resume / Boost / Stop** controls; and upgraded `/admin` with WAI-ARIA tabs (`Overview`, `Tasks & Queue`, `Workspace Files` multi-select sandboxed cleaner, `Storage & Config`), accessible confirmation dialogs, `/admin/api/tasks/action`, and `/admin/api/workspace`.
  - **Newly Added Modules & Tests:**
    - `VidelBot/core/sysinfo.py` — Linux cgroup v2 (`cpu.max`, `memory.max`, `memory.current`) & cgroup v1 (`cpu.cfs_quota_us`, `memory.limit_in_bytes`) container-aware CPU, RAM, disk, and `memory_pressure()` telemetry.
    - `VidelBot/core/diagnostics.py` — Centralized startup configuration validator & secret-free runtime/feature/storage readiness matrix (inspired by WZML-X's `BLOCKED` secret allowlist).
    - `VidelBot/core/settings_portal.py` — Safe `videl-user-settings/1` JSON export/import/reset portal (`/vset export`, `/vset import`, and caller-authorized two-step `uset:*` reset confirmation) across Encoder, Auto-Rename, and Saver preferences with strict `BLOCKED_KEYS` secret/privilege stripping.
    - `VidelBot/VideoEncoder/utils/uploads/{storage,rclone,gofile}.py` — Unified storage provider registry & dispatcher (`telegram`, `gdrive`, `rclone` zero-shell `rclone copy` with remote validation & secret redaction, `gofile` 512 KiB streaming uploader with retry/pause/cancel, and `ddl`) with automatic Telegram fallback when unconfigured.
    - `VidelBot/tests/test_stage_h_wzmlx_upgrades.py` — 12-scenario end-to-end Stage H regression suite (lease fail-closed & heartbeat loss, DB/plugin cache-after-write invariants, payment audit & zero-price guard, SHA-256 backup tamper detection & collection isolation, Drive retry/cancel/cleanup/path safety, paginated `/status` & callback auth, plugin `startup`/`shutdown` timeouts & `required_env`, secret-free diagnostics, `SIGSTOP`/`SIGCONT` pause/resume/boost, Rclone/GoFile validation & streaming retries, `settings_portal` security, memory backpressure, dead-message circuit breaker, and `/admin` workspace sandbox — full suite verified at **`791 passed, 1 skipped, 0 failed`**).
  - **`WZML-X` Features Intentionally Rejected (and Why):**
    - **Torrent / Usenet / Debrid Daemons (`qBittorrent`, `Aria2c RPC`, `SABnzbd/NZB`, `JDownloader`, `Seedr`, `MegaSDK`):** Rejected — turns Videl1 into a heavy mirror/leech seedbox, adds hundreds of MiB of external daemons, and conflicts with Videl1's lawful creator/utility focus.
    - **Multi-Userbot Leech Swarm & Split-Leech Dump Ring:** Rejected — Videl1 already runs multi-tenant FileStore clone workers (`filestore/worker_bot/`) and per-user encrypted `/login` sessions (`saver/`); global userbot swarms risk Telegram flood/account bans.
    - **RSS Auto-Leech Daemon & YouTube Studio Uploader:** Rejected — unrelated to Videl1's Saver / VideoEncoder / Auto-Rename / FileStore / Group-Mod architecture and would breach the `100/100` Telegram `BotCommandScope` ceiling.
    - **Competing Custom Button/Theme Markup DSL (`status_utils` / `button_build`):** Rejected — Videl1 standardizes on `WzGram 3.1.6` + `Richgram 0.2.0` (`core/rich.py` + `core/ui.py`) with native Layer 232 `InputRichMessage` and Bot API 10.3 coloured buttons.
  - **Migration & Config Notes (Zero Breaking Changes):**
    - All new environment variables (`GDRIVE_TOKEN_FILE`, `GDRIVE_SA_FILE`, `GDRIVE_HTTP_TIMEOUT`, `INDEX_URL`, `RCLONE_CONFIG_PATH`, `RCLONE_DEFAULT_REMOTE`, `GOFILE_TOKEN`, `GOFILE_FOLDER_ID`, `SINGLE_INSTANCE_TIMEOUT`, `MEMORY_HIGH_WATERMARK_PCT`, `MEMORY_PAUSE_NEW_TASKS_PCT`) are **optional** with backwards-compatible defaults (`SINGLE_INSTANCE_TIMEOUT=0` preserves rolling-deploy wait handover while failing closed on unreachable MongoDB).
    - Existing `videl-backup/1` archives without `integrity` blocks continue to load cleanly, while all newly created backups include SHA-256 integrity metadata automatically.

### Phase 6 — Multi-Bot SaaS Platform, Advanced Downloads & Media Studio (Stages 1–7)

> **Status correction — not production-ready:** the items below describe code and UI surfaces added, not end-to-end completion of the full product specification. A follow-up security review found material gaps in isolation, runtime integration and verification. Do not deploy this SaaS implementation for real tenants until the unresolved items below have been addressed and independently tested.

- [x] **Platform Core Engine (`VidelBot/platform_core/`):**
  - `models_db.py` — Shared MongoDB + standalone fallback repositories for `platform_tasks`, `download_history`, `upload_history`, `storage_connections`, `scheduled_jobs`, `audit_logs`, and 11-category `webapp_settings` (`general`, `downloads`, `uploads`, `storage`, `media`, `telegram`, `appearance`, `notifications`, `privacy`, `performance`, `security`).
  - `providers.py` — 7 SSRF-guarded legal download adapters (`direct_http`, `github` Releases API, `rss_atom` enclosures, `gdrive` API v3, `dropbox` API v2, `onedrive` Graph v1.0, public `mega` AES-128-CTR) with redirect hop re-validation and multi-file selection sessions.
  - `destinations.py` — 7 upload/delivery adapters (`local` sandboxed per-user workspace, `telegram`, `gdrive`, `dropbox`, `onedrive`, `rclone`, `gofile`) with path-traversal protection.
  - `download_engine.py` & `scheduler_daemon.py` — Priority download queue, per-user concurrency & bandwidth caps, exponential backoff, SHA-256 integrity verification, duplicate detection, and scheduled recurring jobs.
  - `media_studio.py` — Magic-byte validated Video, Audio, Image, PDF (`pypdf==6.21.4`), safe ZIP (Zip-Slip/Zip-Bomb guarded), and MD5/SHA-1/SHA-256 checksum workbench (`tests/test_platform_core.py`).
- [x] **Multi-Workspace SaaS Backend & Security (`VidelBot/webapp/{db_models,auth,services_bots,services_clone_files,services_ops,api_routes}.py`):**
  - **Multi-Workspace & Team RBAC:** Workspace and membership models, 6 roles and 10 permission scopes are present. Isolation and enforcement remain incomplete across several endpoints and user-scoped subsystems; see the blockers below.
  - **Authentication & Secret Protection:** Supports short-lived Telegram links, Mini App `initData`, admin tokens, hashed `vdk_` keys and CSRF validation. New webapp credential writes require a valid Fernet key; existing legacy plaintext secrets are not migrated automatically. The broader secret-handling and RBAC surfaces still need a complete security review.
  - **Bot Studios:** UI and API scaffolding includes creation, config revisions, environment promotion, command metadata, cloning and fleet preview. These do not yet consistently control the actual worker runtime; cloning and rollback are incomplete, and secret exclusion needs further validation.
  - **Files, Analytics & Operations:** Added UI and API scaffolding for files, intelligence, analytics, automation, templates, bundles, demo data and administration. Availability, workspace isolation, actual event integration, import completeness, and persistent concurrency-safe idempotency are not yet established.
- [x] **Reusable Light/Dark SaaS UI Shell (`VidelBot/webapp/{ui_styles,ui_shell,ui_client}.py` & `VidelBot/keep_alive.py`):**
  - Mounted cleanly at `/app` and `/api/v1/*` alongside `/admin`, `/ping`, and `/health` without breaking any existing console or Telegram runtime behavior; includes collapsible sidebar, mobile bottom nav, Workspace & Bot selectors, `⌘K` Command Palette, Custom Dashboard Builder, and 17 interactive Studios (`tests/test_webapp_saas.py` — full suite verified at **`802 passed, 1 skipped, 0 failed`**).

---

## SaaS Feature Matrix — current assessment

Audit baseline: branch `arena/a69b9f62-videl1`, verified remote baseline `fa10c72`; existing VidelBot Telegram runtime, FileStore workers, MongoDB schemas, web APIs, deployment files, tests/CI and WZML-X patterns were reviewed. The table distinguishes scaffolding from proven runtime functionality; Stage 1 remains open.

| Category | Existing | Needs improvement | Missing | Proposed |
| --- | --- | --- | --- | --- |
| Bot management | Workspace records, cards, fleet and credential/runtime status | RBAC, ownership, worker integration and bulk operation safety | Comprehensive worker lifecycle control and telemetry | Reuse the existing receiver and workers; connect and test each supported operation. |
| Bot creation | Guided draft/registration and Telegram token verification | Validate ownership, resilience and end-to-end worker activation | Automated BotFather registration (not possible without user interaction) | Keep explicit BotFather guidance and distinguish verified credentials from running workers. |
| Clone Studio | Scoped preview, explicit confirmation, history and limited guarded bot-record rollback | Secret/private-data exclusion, transactional execution, conflict handling and runtime integration | Safe rollback for commands, File Store configuration, plugins and automation | Use an allowlist and transactions, test each section and failure boundary. |
| Commands | Editor, categories, scopes and metadata | Enforce domain permissions and verify ownership | Reliable application to runtime/Telegram command scopes and real usage | Map only supported commands to existing runtime; preserve 100-command limits. |
| File Store | Namespaced multipart uploads, metadata, trash, policies and intelligence | Legacy migration, crash/concurrency handling, access and backup | Safe copy/share, complete previews and tracking | Migrate explicitly without guessing ownership or deleting existing files; add storage/DB consistency tests. |
| Analytics | File-record aggregates and availability flags | Accurate period metrics, scalable aggregation, access and bot attribution | Workspace-scoped download/failure and actual command telemetry | Instrument events with workspace/bot IDs; show unavailable rather than inferred zero. |
| Automation | Workflow editor, manual execution and schedule scaffolding | Authorization, event wiring, idempotency, loop prevention and job isolation | Durable event pipeline and managed scheduler lifecycle | Implement only supported actions with persistent execution claims and explicit retention. |
| Permissions | Six roles, ten domains, membership and scoped API keys | Full route-by-route RBAC, anti-escalation, isolation and database indexes | Secure invitation lifecycle and complete permission/activity history | Test every read/write and cross-domain path using real MongoDB and adversarial cases. |
| Developer tools | Scoped keys, basic diagnostics and bundle scaffolding | Redaction, scope checks, imports and SSRF review | Complete docs, safe comprehensive backup/restore and runtime diagnostics | Allowlist safe fields, validate ownership and test secret exclusion end to end. |

## Remaining Platform Constraints & Future Follow-Ups
- **Production blockers — tenant isolation:** Downloads, schedules and integrations still use user-scoped rather than consistently workspace-scoped data. Their workspace endpoints remain unsafe for multi-workspace use and need a migration; no production deployment. Analytics, overview and health no longer attribute these legacy histories to workspaces, and workspace activity no longer returns user-wide audit entries. Automatic assignment of legacy bots to the first workspace viewed is disabled, and existing data is retained for an explicit migration. Selected domain reads and cross-domain bundles now require relevant scopes, but other reads and mutation paths still need a route-by-route review. API-key capabilities, deletion and concurrent update behavior need a complete route-by-route audit and adversarial tests. The SaaS index setup function is not currently wired to startup.
- **File Store hardening (partial, not a migration):** New web uploads are isolated from the legacy user-wide download directory and namespaced by workspace, bot IDs are checked, and automatic user-wide file indexing, arbitrary JSON path registration, unsafe copy and unimplemented web sharing are disabled. Legacy records and shared paths are retained but physical rename/purge needs an explicit migration; no data was automatically migrated or deleted. Multipart requests commit each accepted file individually, not atomically as a batch. File/database operations and path checks still need concurrency, crash recovery, storage volume/backup and real MongoDB review. File Store global search respects membership permissions and no longer includes user-wide tasks. Demo data still needs an expiry/cleanup policy.
- **Stage 1 RBAC hardening (partial):** Workspace overview now withholds File Store totals without File Store permission and withholds notifications without logs permission; its unread count covers all workspace unread notifications, not just the latest ten. API key scopes must be held by the creating/rotating administrator, and key management requires integration permission. Membership grants now require a unique workspace/user index and use insert-only updates; role edits and revocations check the previously observed target role. Invalid non-boolean/unknown permissions are rejected. Invitation audit insertion and membership creation are not transactional, actor demotion during an operation is not yet guarded atomically, API-key rotation is not transactional on database failure, and legacy duplicate memberships can cause index creation to fail closed. Real MongoDB concurrency/transaction tests, index startup/migration and a genuine pending-invitation flow remain necessary.
- **Clone Studio progress (partial):** Cross-domain access is required per selected section; execution/rollback require explicit confirmation. Bot-record-only clones have a version/conflict-checked rollback preserving unrelated settings; unsafe rollback is unavailable for legacy operations and operations involving commands, File Store settings, plugins or automations. Permissions-template cloning is not implemented and is no longer offered. Execution across collections and recording rollback status are not transactional; failures can still leave partial changes. Sensitive field names are filtered but free-text content is not reliably screened for credentials or private data, and clone metadata does not comprehensively control Telegram workers. Further redesign, safe allowlists and concurrency testing are required before production.
- **Confirmation safety (partial):** Fleet actions, bundle imports and environment promotion require a literal boolean confirmation; direct production-environment edits also require explicit confirmation. Preview and execution are not bound to an immutable revision and cross-record operations are not transactional, so concurrent changes/failures still need attention.
- **Production blockers — actual runtime behavior:** SaaS bot configurations and command metadata are not comprehensively connected to the existing Telegram workers. Workflow triggers are not wired to all advertised events; scheduled jobs are not automatically started by the application. Clone rollback is unavailable for several sections, bundle import does not restore all exported resources, and several dashboard widgets are placeholders. These capabilities must not be represented as complete.
- **Security follow-up:** This review blocks API keys from unclassified endpoints, protects existing membership records and restricts permission delegation, prevents cross-workspace workflow-ID updates, requires encryption for new webapp bot/integration credentials, and rejects unverified bot tokens through the web API. These targeted fixes do not prove the platform secure. Outbound webhook pings now validate the actual resolver results, block private IPs even with `LEECH_ALLOW_PRIVATE`, disable redirects and hide saved URLs/errors from responses; older saved URLs with query strings or credentials will no longer ping. Webhook URLs are still stored as ordinary configuration and a secret embedded in a path cannot be identified reliably: users must not put credentials there. Other outbound download/provider paths need their own DNS-rebinding audit. Review legacy plaintext credentials, secret redaction inside free-form content, file path/ownership handling, demo data lifetime, and database failure/concurrency handling before deployment. File intelligence no longer labels uninstrumented access counts as usage or zero-byte files as integrity failures; actual access and integrity instrumentation is still missing, and large datasets are sampled.
- **Verification gaps:** Existing automated tests cover a limited set of flows, not all requested unauthorized, concurrency, responsive and live Telegram scenarios. Latest local full suite: 825 passed, 1 warning (with locally installed ffmpeg and ffprobe). Targeted SaaS suite: 30 passed, 1 warning. CI run 41560829263 passed for baseline `8323f4f`; subsequent changes require their own CI verification. Browser screenshots and a production container build have not been verified in this environment.
- **Live Telegram E2E Validation & `@BotFather` Interactive Creation**: Telegram requires interactive owner confirmation inside `@BotFather` (`/newbot`) to mint brand-new bot usernames/tokens; end-to-end verification of live Telegram worker updates, `sendRichMessage`, `setChatMemberTag`, `sendChecklist`, and `ManagedBotUpdated` requires deploying with live `API_ID`, `API_HASH`, `BOT_TOKEN`, `MONGO_URI`, and `ENCRYPTION_KEY`.
- **Optional External Cloud Credentials**: Dropbox (`DROPBOX_ACCESS_TOKEN`), Microsoft OneDrive (`ONEDRIVE_ACCESS_TOKEN`), Google Drive (`GDRIVE_TOKEN_FILE` / `GDRIVE_SA_FILE`), and Rclone (`RCLONE_CONFIG_PATH`) require user-supplied OAuth/API tokens for live external transfers.
- **Process-Level Plugin Isolation**: Approved plugins run in-process with the bot (matching WZML-X's design); only bot owners (`OWNER_ID` / `OWNERS`) can approve staged plugins.
- **Telegram Command Scope Cap**: Telegram caps each `BotCommandScope` at 100 commands (`USER + ADMIN + OWNER = 100/100`; `GROUP_COMMANDS = 46/100`). Adding future private-scope menu entries requires replacing or consolidating existing menu entries (`/web` and `/studio` work directly as private-chat commands without exceeding the 100-command menu ceiling).

## Ultimate Webapp Master Roadmap — requested scope (Sections 1–31)

This is an acceptance checklist, not a claim of completion. Some items have partial scaffolding described in the feature matrix and constraints above; each remains open until its functionality, security and tests are verified. Implement sequentially by stage, completing and testing each stage before expanding. Preserve Videl1's identity and reuse existing working code; study WZML-X and other relevant open-source projects with licensing and attribution review rather than copying indiscriminately.

### 1. Product vision
- [ ] Deliver a responsive, integrated multi-bot SaaS platform for creating, connecting, configuring, cloning, analyzing and operating bots, with the dedicated studios and centers below backed by working services rather than placeholders.

### 2. Audit and feature matrix
- [ ] Keep the nine-category feature matrix above current with the verified target branch/SHA, existing functionality, improvements, missing features and proposals. Re-audit the webapp, frontend/backend, APIs, authentication/authorization, MongoDB schemas, bot registration, File Store, commands, deployments, tests/CI and relevant WZML-X patterns as implementation changes. Do not recreate working features.

### 3. Design system and global navigation
- [ ] Build and verify a reusable, accessible design system with professional typography, spacing, colors, icons, tables, cards, forms, status indicators, subtle motion, light/dark themes, responsive desktop/mobile layouts, and useful empty/loading/error states.
- [ ] Complete collapsible sidebar and mobile navigation, top bar, persisted workspace and bot selectors, global search, command palette, notifications, profile, breadcrumbs, quick actions, help center and theme selector.
- [ ] Avoid generic templates, excessive gradients/glass effects, crowded layouts, arbitrary colors, oversized decoration, inconsistent icons, fabricated charts and nonfunctional controls.

### 4. Multi-workspace system
- [ ] Deliver strictly backend-isolated workspace-owned bots, teams, permissions, templates, File Store configuration, integrations, analytics, notifications, automations, activity and settings.
- [ ] Support workspace creation, rename, icon customization, switching, archive, confirmed deletion, member invitations and roles, activity, dashboard and usage summary. Plan an explicit, non-destructive migration for legacy user-scoped data; do not guess ownership.

### 5. Bot Management Center
- [ ] Show actual bot name, username, profile image, description, type, connection state, runtime health where available, last activity, creation date, features and usage.
- [ ] Support add/connect/register, profile editing, configuration, dashboard, commands, permissions, analytics, logs, clone, supported export, archive, disconnect and delete; add search, sorting, filters, tags, categories and grid/list views. Enforce workspace ownership and authorization on every operation.

### 6. Bot Creation Studio
- [ ] Implement a guided wizard for identity (display name, username, description, image, category, tags), secure token submission, Telegram Bot API validation and identity verification, and connection diagnostics.
- [ ] Offer File Store, utility, group management, media/file management and custom templates; configure features, defaults, command presentation, owner permissions and notifications; include review, validation, confirmation, result and next steps.
- [ ] Guide users through interactive BotFather registration; never imply it can be completed automatically. Distinguish verified credentials from a running bot process.

### 7. Bot Clone Studio
- [ ] For owned/explicitly manageable bots, complete the 11-step flow: source, inspection, section selection, destination or new registration, comparison, conflict detection, preview, validation, confirmation, execution and results.
- [ ] Safely support profile, command metadata, feature, File Store, welcome, UI, notification, permission-template, plugin, automation and custom-text settings where implemented, with independent destination configuration. Never clone tokens, credentials, sessions, payment secrets, private user data or private file ownership.
- [ ] Complete history, progress, validation, diff, exclusions, reusable templates, compatibility checks and migration reports; implement transactional execution and safe rollback where possible. Current rollback is limited and free-text secret/private-data exclusion is not guaranteed.

### 8. Bot Fleet Management
- [ ] Provide all-bot overview with type/status filters, groups and labels, health, recent errors/activity, usage comparison, disconnected bots and configuration differences based on available data.
- [ ] Implement permitted bulk configuration, notification-preference and archive actions with affected-bot preview and explicit confirmation; prevent cross-workspace leakage.

### 9. Bot Configuration Studio
- [ ] Provide searchable, categorized settings, supported feature toggles, validation, defaults, drafts, preview/apply, attributed version history, restore, revision comparison and safe export/import.
- [ ] Explain and validate feature dependencies before activation; connect supported changes to the authoritative runtime.

### 10. Command Management Studio
- [ ] Display name, description, category, visibility, permission, enabled state, available usage and last modification; support add/edit/delete, search/filter/sort, presentation preview and supported bulk metadata changes.
- [ ] Support General, File Store, Administration, Media, Group Management, Utilities, Owner Tools and Custom categories; everyone, owner, administrators, authorized users and supported custom-role scopes.
- [ ] Apply only supported commands to the existing runtime/Telegram and preserve Telegram's 100-command-per-scope ceiling; do not imply metadata creates an executable command.

### 11. File Store Studio
- [ ] Give each owner an isolated workspace and show real file count, storage, uploads/downloads, recent activity, categories, duplicate candidates, growth, failures and available capacity where measurable.
- [ ] Provide table/grid manager, search, sorting, filtering, pagination, previews/details, IDs, sizes, MIME types, dates, metadata, tags, categories, multi-select and supported bulk actions.
- [ ] Support authorized rename/delete/copy/move, metadata/tags/organization, metadata export and permitted sharing where implemented; require deletion confirmation and recovery safeguards where possible.
- [ ] Configure storage channel/destination, allowed types/size, captions/thumbnails, search/pagination, duplicate handling, cleanup and access policies. Address legacy migration, physical storage isolation and database/filesystem consistency first.

### 12. File Intelligence Center
- [ ] Provide largest/recent/frequently accessed files, duplicate candidates, type distribution, growth, missing metadata, integrity issues and unused files only where tracking supports them.
- [ ] Search by filename, ID, MIME type, size, date, tags, category and metadata; analyze trends, destination distribution, growth forecasts, counts, large files and duplicate-space estimates using real collected data.
- [ ] Never automatically delete files based on a guessed cleanup recommendation; label estimates and unavailable information accurately.

### 13. File Store Owner Analytics
- [ ] Provide private owner metrics for file count/storage, upload/download trends, popular files/categories, recent operations, measurable active users, failures, health and usage patterns.
- [ ] Add today, 7/30/90-day and custom ranges, CSV export and reports; distinguish unavailable data from zero and verify workspace/bot attribution.

### 14. Bot Analytics
- [ ] Record and show per-bot user activity where available, command usage, operation success/failure, tasks, uploads/downloads, storage, runtime availability and errors.
- [ ] Add accurate activity, command popularity, transfer, storage growth, success/failure and trend charts with date filtering and period comparisons; never fabricate metrics or Telegram data.

### 15. Bot Health and Monitoring
- [ ] Show token/API connectivity separately from runtime status, last successful operation/error, available worker/plugin health, relevant database connectivity, warnings and health history.
- [ ] Add alerts for connection failure, offline bots, repeated errors, storage and integration warnings, and failed scheduled jobs, only where observable.

### 16. Bot Environment Manager
- [ ] Implement separately stored Development, Testing and Production configurations, labels, comparison, environment-specific integrations and health, and change history.
- [ ] Provide safe promotion with preview and explicit approval; never automatically overwrite production with development settings.

### 17. Webhook and Event Monitor
- [ ] Show supported outbound/integration webhook configuration status, endpoint health, observed successful/failed deliveries, errors, retries and recent filterable events, with diagnostics and secret-safe storage/logging.
- [ ] Preserve the authoritative Telegram update architecture; do not introduce a second incoming Telegram update receiver.

### 18. Automation Studio
- [ ] Build a supported Trigger → Conditions → Actions → Result editor with trigger/condition/action selection, preview, validation, enable/disable, execution history, retry, errors, duplicate and version history.
- [ ] Wire only supported workflows: file/activity notifications, download completion, storage alerts, reports, bot errors, approved bot initialization, policy-based retry and inspection of eligible cleanup candidates.
- [ ] Implement durable idempotency, execution limits, loop prevention, authorization and event wiring; do not expose unimplemented actions as operational.

### 19. Scheduled Task Center
- [ ] Support create/edit/pause/resume/delete, timezone, next/last run, history and failure details with workspace isolation and a managed scheduler lifecycle.
- [ ] Enable only implemented reports, notifications, backups, approved cleanup and health checks; ensure scheduling does not cause unsafe automatic deletion.

### 20. Template Library and Marketplace
- [ ] Support official and user templates for bot/File Store configuration, command metadata, permission presets, automations, welcome messages, dashboard layouts and notification preferences.
- [ ] Add create/edit/duplicate, preview, safe import/export, version history and compatibility checks. Optional community marketplace: submission, review/moderation, publisher, version, installation preview and reporting.
- [ ] Templates must not silently execute arbitrary code or include credentials/private data.

### 21. Plugin and Extension Center
- [ ] Show supported plugins' name, description, version, compatibility, dependencies, permissions, enabled state, configuration, diagnostics and changelog; implement supported enable/disable/configure/health operations.
- [ ] Treat a reviewed extension marketplace as future work; do not run untrusted arbitrary code. Existing owner-approved in-process plugins are not isolation from malicious code.

### 22. Team and Access Management
- [ ] Support Owner, Administrator, Manager, Developer, Analyst and Viewer with independently configurable bot settings, commands, File Store, analytics, integrations, logs, templates, cloning, automation and team permissions.
- [ ] Complete invitation/pending invitation, revocation, role changes, member activity and permission history; enforce backend anti-escalation and protect ownership changes, concurrent updates and every cross-domain operation.

### 23. Developer Center
- [ ] Provide API documentation/examples, scoped API keys with expiration, rotation and revocation, webhook/connection diagnostics, configuration validation, bot diagnostics, error inspection, test environment and available usage information.
- [ ] Reveal new secrets only once; never redisplay saved secrets or expose production credentials in developer tools. Enforce scope delegation and workspace isolation.

### 24. Configuration Migration Center
- [ ] Support owned source/destination selection, compatibility, comparison, conflict detection, selective migration, preview, confirmation, progress, report and rollback where safe.
- [ ] Migrate only supported command metadata, feature/File Store settings, templates, notification preferences and automation workflows; never migrate credentials or private user data.

### 25. Custom Dashboard Builder
- [ ] Support bot health, fleet, recent activity, File Store usage, storage trends, active tasks, command usage, integration health, failed operations, scheduled jobs and notifications using actual authorized data.
- [ ] Add/remove/reorder widgets, resize where supported, save/reset layouts and multiple presets; persist separately per user and workspace.

### 26. Notification and Activity Center
- [ ] Provide supported bot/connection/task/job/File Store/storage/integration/permission/configuration/clone/automation/report notifications with read/unread, filters, search, preferences and per-bot/workspace views.
- [ ] Provide searchable permission-aware audit history with actor, action, resource, timestamp and outcome; never log secrets.

### 27. Global Search and Command Palette
- [ ] Search authorized bots, files, commands, templates, tasks, integrations, settings and activity; add keyboard navigation, recent searches/resources, suggestions, quick navigation and permitted actions.

### 28. Export, Import and Backup
- [ ] Safely export/import supported bot configuration, command metadata, templates, File Store settings, automations and dashboard layouts with preview, validation, compatibility, conflict detection, confirmation and history.
- [ ] Implement tested configuration backup/restore; never export tokens, credentials or private user information. Current bundle import is incomplete.

### 29. Platform Admin Center
- [ ] Restrict platform administration to platform administrators and separate it from workspace administration; provide authorized user/workspace/bot management, platform usage, system/API/database/storage/worker/plugin health, error/audit logs, maintenance and feature availability.

### 30. Onboarding, Help and Product Experience
- [ ] Complete interactive onboarding, getting-started checklist, clearly labeled demo workspace, contextual help, documentation, searchable help, changelog, What's New and feedback; offer support tickets only if supported.
- [ ] Add keyboard shortcuts, favorites, recent bots/files, saved filters, unsaved-change warnings, validation, tooltips, copy controls and useful empty states. Define demo-data expiry and cleanup.

### 31. Optional future premium roadmap
- [ ] Evaluate custom branding/layouts, advanced analytics, more team seats, configurable task limits, extra environments/integration slots, report presets, audit retention, template publishing and collaboration as optional future work.
- [ ] Do not introduce real payment/subscription handling without an approved billing architecture and secure implementation plan; do not artificially restrict existing features.

### Cross-cutting delivery and safety gates
- [ ] Keep work on `arena/a69b9f62-videl1`; do not modify `main`, merge, deploy, delete branches or alter production services without explicit permission. Use focused commits and update this roadmap honestly.
- [ ] Preserve the WzGram/Pyrofork authoritative incoming receiver, aiogram outgoing Bot API, MongoDB and Richgram architecture. Keep one canonical root `Dockerfile` and synchronized root and `VidelBot/` requirements.
- [ ] Enforce backend workspace isolation, bot ownership and RBAC; prevent IDOR, CSRF and abuse; encrypt secrets at rest and prevent secret exposure in URLs, responses, logs, clones and exports. Never bypass DRM, paywalls, CAPTCHAs or platform restrictions.
- [ ] Complete and test stages sequentially; verify full tests, production build, responsive screenshots, real MongoDB concurrency and live Telegram end-to-end flows when feasible. Report gaps and observed results without claiming unverified functionality, builds or CI.
