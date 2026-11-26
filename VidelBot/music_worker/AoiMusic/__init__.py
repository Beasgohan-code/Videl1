"""Archived upstream single-bot application; intentionally not executable.

Videl Music clones are run by :mod:`music_worker.engine`, which provides
per-clone credentials, handlers, storage, and lifecycle. This legacy package
is retained for source provenance and its MIT license, but importing it would
start global bot/session side effects and is therefore blocked.
"""

raise RuntimeError(
    "The legacy global Music application is disabled. Start the Videl clone "
    "worker with MUSIC_CLONE_WORKER=1 python -m music_worker instead."
)
