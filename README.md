# Videl1

Production-grade multi-function Telegram bot platform in **[`VidelBot/`](VidelBot/)**, unifying **Restricted Content Saver**, **FFmpeg / GPU Video Encoder**, **Auto-Rename Pipeline**, **Multi-Tenant FileStore Clone Engine**, **Live Group Moderation**, **Telegram Stars Commerce**, and an **Owner-Governed YAML Plugin System**.

---

## Architecture Overview

```
Telegram Cloud (MTProto Layer 232 + Bot API 10.3; optional 10.4 widget beta)
        │
        ▼  (WzGram primary; optional widget-query-only beta listener)
┌───────────────────────────────────────────────────────────────────┐
│  WzGram 3.1.6 Client (`VidelBot/client.py` + `WarpCrypto 2.1.0`)  │
│   group -20  core/middleware.py (dedupe · ban · maintenance)      │
│   group -10  core/payments.py   (atomic idempotent Stars XTR)     │
│   group  -4  core/middleware.py (user tracking · referral credit) │
│   group  -2  core/fsub.py       (parallel force-subscribe gate)   │
│   group   0  saver · encoder · renamer · filestore · plugins      │
└───────────────┬───────────────────────────────────┬───────────────┘
                │ outgoing Bot API 10.3; optional    │ shared Motor pool
                │ widget-query-only beta polling    │
                ▼                                   ▼
┌─────────────────────────────────┐ ┌───────────────────────────────┐
│ `core/botapi.py` (aiogram 3.31) │ │ MongoDB (`videl`, `saver`,    │
│ • Richgram 3-tier `Doc` UI      │ │ `encoder`, `filestore`)       │
│ • Ephemeral replies & drafts    │ │ • `core/instance.py` lease    │
│ • Coloured / copy / pickers     │ │ • Unique `payments.charge_id` │
└─────────────────────────────────┘ └───────────────────────────────┘
```

- **Primary Update Consumer:** `WzGram` receives normal commands and callbacks. The optional Bot API 10.4 widget beta adds a separate, explicitly enabled poller for `widget_query` / `shared_widget_query` only; it refuses to start when a webhook is configured. No normal updates are routed through that poller. `core/instance.py` maintains a **fail-closed** MongoDB heartbeat lease (`InstanceLeaseError` on unreachable DB or timeout).
- **Unified Task Telemetry & Rich UI (`core/rich.py`, `core/progress.py`, `VideoEncoder/utils/jobs.py`):** One centralized `richgram`-compatible `Doc` builder renders across Bot API 10.3 `sendRichMessage`, native WzGram `InputRichMessage`, and classic HTML fallback, powering live paginated `/status` cards (filters, progress bars, speed, ETA, inline per-task pause/resume/boost/cancel across Encoder, Auto-Rename, and Content Saver) and the responsive `/admin` web console.
- **Clone Worker Engine (`filestore/worker_bot/`):** User-created FileStore clone bots run as isolated in-memory `WzGram` clients with encrypted tokens, per-clone `bot_<id>_*` collections, and atomic Stars payment idempotency.
- **Resilient Storage, Settings Portal & Secret-Free Diagnostics (`VideoEncoder/utils/uploads/`, `core/settings_portal.py`, `core/diagnostics.py`):** Pluggable storage registry (`Telegram`, `Google Drive`, `Rclone`, `GoFile`, `DDL Mirrors`) with automatic Telegram fallback, portable user settings export/import/reset (`/vset export` · `/vset import`), container cgroup v1/v2 resource & memory-backpressure telemetry (`core/sysinfo.py`), and secret-free feature diagnostics.

---

## Documentation

- **[`VidelBot/README.md`](VidelBot/README.md)** — Full feature reference, startup/shutdown lifecycle, Bot API compatibility matrix, plugin development & staged installation guide, MongoDB schema & indexes, backup/dry-run restore guide, troubleshooting, and Telegram API limitations.
- **[`TODO.md`](TODO.md)** — Engineering roadmap, completed audit & hardening milestones, and verification metrics.

---

## Quick Deploy (Single Root `Dockerfile`)

All container deployments build from the single canonical **`./Dockerfile`** at the repository root:

| Platform | Configuration |
|---|---|
| Docker / VPS | `docker compose up -d --build` (`docker-compose.yml` + `./Dockerfile`) |
| Railway | `railway.toml` (`dockerfilePath = "Dockerfile"`) |
| Render | `render.yaml` (`dockerfilePath: ./Dockerfile`) |
| Heroku | `heroku.yml` + `app.json` |
| Koyeb / Fly.io / Northflank | Root `Dockerfile` |

---

## Local Development & Testing

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r VidelBot/requirements.txt -r VidelBot/requirements-dev.txt
# Ensure wzgram owns the pyrogram namespace (richgram pulls kurigram as a metadata dep):
pip uninstall -y kurigram && pip install --no-deps --force-reinstall wzgram==3.1.6

cd VidelBot
python -m pytest tests -q
```

> Legacy prototype directories in the repository root (`modules/`, `helper/`, `info/`) are historical artifacts not imported by `VidelBot/` and excluded from container builds via `.dockerignore`.
