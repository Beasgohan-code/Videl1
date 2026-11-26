"""Run the standalone Videl Music application.

The previous clone-aware worker modules remain available for migration
compatibility; the default standalone entry point now uses the imported
music application and its single-bot configuration model.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent


def main() -> None:
    if os.environ.get("MUSIC_WORKER_STANDALONE", "").strip() != "1":
        raise SystemExit(
            "Refusing to start: set MUSIC_WORKER_STANDALONE=1 to confirm this "
            "is the only Music worker process."
        )
    os.chdir(PACKAGE_ROOT)
    sys.path.insert(0, str(PACKAGE_ROOT))
    from AoiMusic.__main__ import init

    asyncio.run(init())


if __name__ == "__main__":
    main()
