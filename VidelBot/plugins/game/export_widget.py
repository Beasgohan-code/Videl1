"""Export a trusted built-in widget for Telegram's offline sandbox harness.

Run from VidelBot: python -m plugins.game.export_widget live /tmp/videl-live.html
Only built-in keys are accepted. Existing files are never overwritten.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import zlib

from plugins.game.plugin import WIDGET_KEYS, widget_html


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("key", choices=sorted(WIDGET_KEYS))
    parser.add_argument("output", type=Path, help="new HTML file; must not already exist")
    args = parser.parse_args(argv)
    raw = widget_html(args.key).encode("utf-8")
    packed = len(zlib.compress(raw, 9))
    if len(raw) > 32768 or packed > 4096:
        parser.error("assembled widget exceeds Telegram's size limits")
    try:
        with args.output.open("xb") as file:
            file.write(raw)
    except FileExistsError:
        parser.error("output already exists; choose a new path")
    print(f"Exported {args.key}: {len(raw)} bytes, {packed} bytes zlib-9")


if __name__ == "__main__":
    main()
