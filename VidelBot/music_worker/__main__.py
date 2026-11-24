"""Standalone Music worker program.

    cd VidelBot && MUSIC_WORKER_STANDALONE=1 python -m music_worker

Runs every clone whose profile is ``music`` using ONLY this package —
a separate worker program, like the File Store worker, not a plugin of
the main bot.

Safety: this process must be the ONLY one serving those clones, so the
launcher refuses to start unless ``MUSIC_WORKER_STANDALONE=1`` is set.
``run.py`` does not import this module. Note the stage-1 limit from
this package's README: voice-chat streaming is not enabled yet, so
playback commands queue honestly instead of playing.
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
    await stop_event.wait()

    log.info("stopping…")
    stopped = await music_worker_engine.stop_all()
    log.info("🛑 stopped %d clone(s)", stopped)
    from filestore.database.mongo import close_motor_client
    close_motor_client()


if __name__ == "__main__":
    asyncio.run(_main())
