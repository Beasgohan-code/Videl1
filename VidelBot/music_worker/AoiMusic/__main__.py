"""Disabled legacy launcher; Music is now served per clone by Videl."""
raise SystemExit(
    "The legacy global Music launcher is disabled. Start the Videl Music "
    "clone worker with MUSIC_CLONE_WORKER=1 python -m music_worker."
)
