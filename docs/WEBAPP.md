# Videl1 Studio webapp

## What this app is

Videl1 Studio is the multi-workspace SaaS interface served by the Telegram bot's aiohttp process. The Python package in `VidelBot/webapp/` contains the API, auth/session handling, persistence models, domain services, and browser UI. `/app` serves the shell, `/app/styles.css` serves the design system, `/app/app.js` serves the client, and `/api/v1/*` serves the API. The separate Vercel project is a proxy/front door; it is not an independent backend or test environment.

The `/admin` classic operations console is separate from `/app`. A feature appearing in Studio does not by itself establish that the relevant bot runtime, scheduled worker, or data migration is connected; use the capability and production caveats in [`AUDIT.md`](AUDIT.md) and the root [`TODO.md`](../TODO.md) as the status source.

## Product benchmark

TeleBotHost’s public site emphasizes a short “connect a BotFather token, write commands, launch” path. Its documentation describes a JavaScript-based TBL workflow, an isolated execution sandbox, a built-in database, and webhook/webapp support ([product site](https://telebothost.com/), [documentation](https://docs.telebothost.com/)). Videl1 borrows the high-level focus on an understandable bot-building journey, not the competitor’s brand, source, code, or assets. Its actual bot runtime remains based on Videl1’s existing Pyrofork/Aiogram architecture; don’t imply that a general code sandbox exists until one is built and audited.

## UI conventions

- Keep workspace- and bot-scoped views explicit; preserve the selected workspace/bot behavior.
- Use existing API responses as the only source for displayed metrics. Show unavailable/permission-restricted data as unavailable, never as a fabricated zero.
- Escape API/user-provided text before inserting it into HTML. Keep authenticated mutations behind the existing CSRF-aware `api()` helper.
- Add controls only when their action is implemented. Destructive or cross-record operations need an explicit confirmation and server-side authorization.
- Preserve keyboard use, visible focus, reduced-motion preferences, responsive behavior, and light/dark tokens in `ui_styles.py`.
- Never claim that a client-side control is a security boundary; authorization and tenant isolation must be enforced server-side.

## Recent UI accessibility improvements

The Overview now opens with an original Videl Studio command-center hero and a compact workspace snapshot. Its bot and File Store counts come from current workspace records and the permissions-filtered API response. Bot status is explicitly described as “marked running,” not verified live polling. The existing customizable widget grid and “unavailable” states remain intact.

The shared shell also has a skip-to-workspace link and named navigation landmarks. The global search palette exposes combobox/listbox semantics, supports Up/Down and Enter, keeps the active result visible, and returns focus to the opening control when dismissed. The shared CSS honors reduced-motion preferences and device safe-area insets on the mobile bottom bar. These changes do not add backend capabilities or imply production readiness.

## Local verification

From the repository root, install the dependencies declared in `VidelBot/requirements.txt` and `VidelBot/requirements-dev.txt`, then run:

```bash
cd VidelBot
python -m pytest tests/test_webapp_saas.py -q
python -m pytest tests -q
```

Tests use the repository harness and an in-memory database by default. They do not substitute for concurrency testing against real MongoDB, live Telegram worker verification, deployment validation, or an independent security review. Use a disposable test environment and test credentials only; do not exercise write/delete behavior against production data.
