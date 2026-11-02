# Videl1

The active bot lives in **[`VidelBot/`](VidelBot/)**, powered by **WzGram v3.1.6** (MTProto Layer 232 + **WarpCrypto**), **Richgram v0.2.0**, **aiogram 3.31** (Bot API 10.3), **Richgram-styled Group Moderation**, and an owner-governed **WZML-X-inspired YAML plugin system** (`/plugins`, `/imdb`, `/anime`, `/manga`, `/character`, `/mdl`, `/exportsession`, `/gsettings`).

See **[VidelBot/README.md](VidelBot/README.md)** for all features, plugin & moderation documentation, and the full deploy guide, and **[TODO.md](TODO.md)** for milestone status and platform notes.

**Quick deploy** (all use the root `Dockerfile`):

| Platform | File |
|---|---|
| Railway | `railway.toml` |
| Render | `render.yaml` (Blueprint) |
| Heroku | `heroku.yml` + `app.json` |
| Koyeb / Fly.io / Northflank | `Dockerfile` |
| VPS | `docker-compose.yml` |

The remaining files in the repository root are the older, unfinished Videl prototype. VidelBot does not use them.
