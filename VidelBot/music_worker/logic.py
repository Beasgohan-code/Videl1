"""Pure queue/settings logic for the clone Music worker (no Telegram deps)."""
from __future__ import annotations

from collections import OrderedDict
import math
import random


def default_settings() -> dict:
    return {
        "dj_mode": False,      # only admins may queue/skip/stop
        "volume": 100,         # 10..200
        "autoplay": False,     # queue extra search hits after a /play title
        "loop_mode": "off",   # off | one | all
        "speed": 1.0,          # playback speed 0.25..4.0 (ffmpeg atempo)
        "linked_channel": 0,   # channel chat_id for /cplay (0 = none)
    }


def normalize_settings(values) -> dict:
    """Return safe persisted Music settings, falling back to defaults per field."""
    defaults = default_settings()
    if not isinstance(values, dict):
        return defaults
    out = dict(defaults)
    for key in ("dj_mode", "autoplay"):
        if isinstance(values.get(key), bool):
            out[key] = values[key]
    volume = values.get("volume")
    if isinstance(volume, int) and not isinstance(volume, bool):
        out["volume"] = min(200, max(10, volume))
    loop_mode = values.get("loop_mode")
    if loop_mode in ("off", "one", "all"):
        out["loop_mode"] = loop_mode
    speed = values.get("speed")
    if (isinstance(speed, (int, float)) and not isinstance(speed, bool)
            and math.isfinite(float(speed))):
        out["speed"] = min(4.0, max(0.25, float(speed)))
    linked_channel = values.get("linked_channel")
    if (isinstance(linked_channel, int) and not isinstance(linked_channel, bool)
            and -(2**63) < linked_channel < 2**63):
        out["linked_channel"] = linked_channel
    return out


def ffmpeg_params(seek: float = 0, speed: float = 1.0) -> str | None:
    """ffmpeg parameters for seek/speed, or None for a plain stream."""
    parts = []
    if seek and float(seek) > 0:
        parts.append(f"-ss {int(float(seek))}")
    if speed and abs(float(speed) - 1.0) > 1e-6:
        parts.append(f"-filter:a atempo={float(speed):g}")
    return " ".join(parts) or None


def format_duration(seconds: int | float | None) -> str:
    if not seconds or seconds <= 0:
        return "live"
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class PlayQueue:
    """Bounded per-chat track queue; index 0 is the current track."""

    def __init__(self, max_tracks: int = 100):
        self._queues: "OrderedDict[int, list[dict]]" = OrderedDict()
        self._max = max(1, int(max_tracks))

    def enqueue(self, chat_id: int, track: dict) -> int:
        queue = self._queues.setdefault(int(chat_id), [])
        queue.append(dict(track))
        if len(queue) > self._max:
            del queue[1:len(queue) - self._max + 1]  # keep current + newest
        self._queues.move_to_end(int(chat_id))
        return len(queue) - 1  # position (0 = now playing)

    def snapshot(self, chat_id: int) -> list[dict]:
        return [dict(t) for t in self._queues.get(int(chat_id)) or []]

    def restore(self, chat_id: int, tracks: list[dict]) -> None:
        clean = [dict(t) for t in (tracks or []) if isinstance(t, dict)][-self._max:]
        if clean:
            self._queues[int(chat_id)] = clean
        else:
            self._queues.pop(int(chat_id), None)

    def now_playing(self, chat_id: int) -> dict | None:
        queue = self._queues.get(int(chat_id)) or []
        return queue[0] if queue else None

    def upcoming(self, chat_id: int, limit: int = 10) -> list[dict]:
        queue = self._queues.get(int(chat_id)) or []
        return list(queue[1:1 + limit])

    def play_now(self, chat_id: int, track: dict) -> int:
        """Insert a track as the current one (queue keeps its order after it)."""
        queue = self._queues.setdefault(int(chat_id), [])
        queue.insert(0, dict(track))
        if len(queue) > self._max:
            del queue[self._max:]
        self._queues.move_to_end(chat_id)
        return 0

    def clear_upcoming(self, chat_id: int) -> int:
        """Drop everything except the current track; returns drop count."""
        queue = self._queues.get(int(chat_id)) or []
        dropped = max(0, len(queue) - 1)
        if dropped:
            del queue[1:]
        return dropped

    def skip(self, chat_id: int) -> dict | None:
        queue = self._queues.get(int(chat_id)) or []
        if queue:
            queue.pop(0)
        return queue[0] if queue else None

    def advance(self, chat_id: int, loop_mode: str = "off") -> dict | None:
        """Apply a natural-end transition while keeping loop mutations bounded."""
        queue = self._queues.get(int(chat_id)) or []
        if not queue:
            return None
        if loop_mode == "one":
            return queue[0]
        current = queue.pop(0)
        if loop_mode == "all":
            queue.append(current)
        return queue[0] if queue else None

    def shuffle_upcoming(self, chat_id: int) -> int:
        """Shuffle only pending tracks, never changing the current track."""
        queue = self._queues.get(int(chat_id)) or []
        pending = queue[1:]
        random.shuffle(pending)
        queue[1:] = pending
        return len(pending)

    def remove(self, chat_id: int, position: int) -> dict | None:
        """Remove a 1-based upcoming position; the current track is protected."""
        queue = self._queues.get(int(chat_id)) or []
        index = int(position)
        if 1 <= index < len(queue):
            return queue.pop(index)
        return None

    def move_upcoming(self, chat_id: int, source: int, destination: int) -> bool:
        """Move between 1-based upcoming positions without moving the current track."""
        queue = self._queues.get(int(chat_id)) or []
        source, destination = int(source), int(destination)
        if not (1 <= source < len(queue) and 1 <= destination < len(queue)):
            return False
        if source == destination:
            return True
        track = queue.pop(source)
        queue.insert(destination, track)
        return True

    def clear(self, chat_id: int) -> int:
        queue = self._queues.pop(int(chat_id), [])
        return len(queue)

    def size(self, chat_id: int) -> int:
        return len(self._queues.get(int(chat_id)) or [])
