"""Pure queue/settings logic for the clone Music worker (no Telegram deps)."""
from __future__ import annotations

from collections import OrderedDict


def default_settings() -> dict:
    return {
        "dj_mode": False,      # only admins may queue/skip/stop
        "volume": 100,         # 10..200
        "autoplay": False,     # keep playing related tracks (future)
    }


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

    def skip(self, chat_id: int) -> dict | None:
        queue = self._queues.get(int(chat_id)) or []
        if queue:
            queue.pop(0)
        return queue[0] if queue else None

    def remove(self, chat_id: int, position: int) -> dict | None:
        """Remove by 1-based queue position (1 = current)."""
        queue = self._queues.get(int(chat_id)) or []
        index = int(position) - 1
        if 0 <= index < len(queue):
            return queue.pop(index)
        return None

    def clear(self, chat_id: int) -> int:
        queue = self._queues.pop(int(chat_id), [])
        return len(queue)

    def size(self, chat_id: int) -> int:
        return len(self._queues.get(int(chat_id)) or [])
