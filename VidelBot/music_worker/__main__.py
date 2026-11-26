"""Standalone Music worker program.

    cd VidelBot && MUSIC_WORKER_STANDALONE=1 python -m music_worker

Runs every active clone whose profile is ``music`` using ONLY this
package — a separate worker program, not a plugin of the main bot.
Playback uses the saved assistant session when configured and degrades
honestly to queue-only mode when voice setup is unavailable.

Safety: run this as the sole Music worker for these clones. The launcher
refuses to start unless ``MUSIC_WORKER_STANDALONE=1`` is set. ``run.py``
does not import this module.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.chdir(Path(__file__).resolve().parent.parent)

import config  # noqa: E402,F401  (loads config.env – must be first)
import logger  # noqa: E402,F401  (configures logging)

log = logging.getLogger("music_worker")


async def _main() -> None:
    if os.environ.get("MUSIC_WORKER_STANDALONE", "").strip() != "1":
        log.error(
            "Refusing to start: set MUSIC_WORKER_STANDALONE=1 to confirm this "
            "process is the only one serving Music clones.")
        sys.exit(2)

    from filestore.database.mongo import get_motor_client
    await get_motor_client().admin.command("ping")
    log.info("✅ MongoDB connected")

    from music_worker.engine import music_worker_engine
    await music_worker_engine.start_all()
    log.info("🎵 %d Music clone(s) running", music_worker_engine.active_count)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:  # Windows / restricted environments
            signal.signal(sig, lambda *_: stop_event.set())
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            try:
                result = await music_worker_engine.reconcile()
                if result["failed"]:
                    log.warning("Music reconciliation has %d failed clone(s).", len(result["failed"]))
            except Exception:
                log.warning("Music profile reconciliation could not be completed.", exc_info=True)

    log.info("stopping…")
    stopped = await music_worker_engine.stop_all()
    log.info("🛑 stopped %d clone(s)", stopped)
    from filestore.database.mongo import close_motor_client
    close_motor_client()


if __name__ == "__main__":
    asyncio.run(_main())
