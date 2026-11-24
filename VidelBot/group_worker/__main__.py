"""Standalone Group Management worker program.

    cd VidelBot && GROUP_WORKER_STANDALONE=1 python -m group_worker

Runs every clone whose profile is ``group`` using ONLY this package —
a separate worker program, like the File Store worker, not a plugin of
the main bot.

Safety: this process must be the ONLY one serving those clones. The
main bot (``run.py``) also starts group clones today, so running both
at once would answer every update twice. The launcher therefore
refuses to start unless ``GROUP_WORKER_STANDALONE=1`` is set, which is
the operator confirming the main process no longer serves group
clones. ``run.py`` does not import this module.
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

log = logging.getLogger("group_worker")


async def _main() -> None:
    if os.environ.get("GROUP_WORKER_STANDALONE", "").strip() != "1":
        log.error(
            "Refusing to start: set GROUP_WORKER_STANDALONE=1 to confirm this "
            "process is the only one serving Group Management clones (the main "
            "bot must not serve them at the same time).")
        sys.exit(2)

    from filestore.database.mongo import get_motor_client
    await get_motor_client().admin.command("ping")
    log.info("✅ MongoDB connected")

    from group_worker.engine import group_worker_engine
    await group_worker_engine.start_all()
    log.info("🛡 %d Group Management clone(s) running", group_worker_engine.active_count)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:  # Windows / restricted environments
            signal.signal(sig, lambda *_: stop_event.set())
    await stop_event.wait()

    log.info("stopping…")
    stopped = await group_worker_engine.stop_all()
    log.info("🛑 stopped %d clone(s)", stopped)
    from filestore.database.mongo import close_motor_client
    close_motor_client()


if __name__ == "__main__":
    asyncio.run(_main())
