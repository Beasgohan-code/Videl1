"""Run the clone-aware Music profile worker.

Start from ``VidelBot/`` with ``MUSIC_CLONE_WORKER=1 python -m music_worker``.
This is a separate process from the main controller and File Store workers;
it reconciles only active ``clone_profile=music`` records from MongoDB.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parent
VIDELBOT_ROOT = PACKAGE_ROOT.parent
log = logging.getLogger("music_worker")


def _sync_interval() -> float:
    raw = os.environ.get("MUSIC_WORKER_SYNC_SECONDS", "10")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise SystemExit("MUSIC_WORKER_SYNC_SECONDS must be a number between 2 and 300") from None
    if not 2 <= value <= 300:
        raise SystemExit("MUSIC_WORKER_SYNC_SECONDS must be between 2 and 300")
    return value


async def serve(engine: Any, stop_event: asyncio.Event, *, sync_interval: float = 10) -> None:
    """Start, periodically reconcile desired clone state, then stop cleanly."""
    try:
        await engine.start_all()
        log.info("🎵 %d Music clone(s) running", engine.active_count)
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=sync_interval)
            except asyncio.TimeoutError:
                try:
                    result = await engine.reconcile()
                    if any(result.get(key) for key in ("started", "stopped", "failed")):
                        log.info("Music clone sync: %d started, %d stopped, %d failed",
                                 len(result.get("started", ())),
                                 len(result.get("stopped", ())),
                                 len(result.get("failed", ())))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Keep the process alive through transient database outages;
                    # the next serialized pass will converge again.
                    log.exception("Music clone reconciliation failed; retrying next interval")
    finally:
        stopped = await engine.stop_all()
        log.info("🛑 Music worker shutdown requested for %d clone(s)", stopped)


async def _main() -> None:
    if os.environ.get("MUSIC_CLONE_WORKER", "").strip() != "1":
        raise SystemExit(
            "Refusing to start: set MUSIC_CLONE_WORKER=1 to confirm this is the "
            "single configured Music-profile worker process. This is an operator "
            "guard, not a distributed lease."
        )

    os.chdir(VIDELBOT_ROOT)
    sys.path.insert(0, str(VIDELBOT_ROOT))
    import config  # noqa: E402 - loads VidelBot/config.env
    import logger as _videl_logger  # noqa: E402,F401 - configure shared logging

    if not config.CLONE_PROFILES_SINGLE_INSTANCE:
        raise SystemExit(
            "Music clone profiles are disabled: set CLONE_PROFILES_SINGLE_INSTANCE=True "
            "only when the deployment is configured for a single clone worker instance."
        )
    if type(config.API_ID) is not int or config.API_ID <= 0 or not config.API_HASH:
        raise SystemExit("The Music worker requires Videl's valid API_ID and API_HASH settings.")
    if not config.MONGO_URI:
        raise SystemExit("The Music worker requires Videl's shared MONGO_URI/DB_URI setting.")

    from filestore.utils.security import is_encryption_enabled
    if not is_encryption_enabled():
        raise SystemExit(
            "The Music worker requires the valid ENCRYPTION_KEY already used by Videl "
            "to protect clone bot tokens and assistant sessions."
        )

    from filestore.database.mongo import close_motor_client, get_motor_client
    await get_motor_client().admin.command("ping")
    log.info("✅ MongoDB connected")

    from music_worker.engine import music_worker_engine

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):  # Windows / restricted environments
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop_event.set))

    try:
        await serve(music_worker_engine, stop_event, sync_interval=_sync_interval())
    finally:
        from core.bg import cancel_all
        try:
            await cancel_all(timeout=5.0)
        finally:
            close_motor_client()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
