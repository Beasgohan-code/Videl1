"""Mongo-backed settings for the clone Music worker, scoped per clone+chat."""
from __future__ import annotations

from .logic import default_settings


_TRACK_FIELDS = ("title", "duration", "requested_by", "source")


class MusicWorkerStore:
    def __init__(self, db):
        self._settings = db["music_worker_settings"]
        self._queues = db["music_worker_queues"]
        self._playlists = db["music_worker_playlists"]

    # ── personal playlists (per clone + user) ───────────────────
    @staticmethod
    def _pkey(bot_id: int, user_id: int, name: str) -> dict:
        return {"bot_id": int(bot_id), "user_id": int(user_id),
                "name": str(name or "").strip().lower()[:40]}

    async def create_playlist(self, bot_id: int, user_id: int, name: str) -> dict:
        key = self._pkey(bot_id, user_id, name)
        await self._playlists.update_one(key, {"$setOnInsert": {"tracks": []}}, upsert=True)
        return await self.get_playlist(bot_id, user_id, name) or {**key, "tracks": []}

    async def get_playlist(self, bot_id: int, user_id: int, name: str) -> dict | None:
        doc = await self._playlists.find_one(self._pkey(bot_id, user_id, name))
        if not doc:
            return None
        return {"name": doc.get("name", ""),
                "tracks": [{k: t.get(k) for k in _TRACK_FIELDS}
                           for t in doc.get("tracks") or [] if isinstance(t, dict)]}

    async def list_playlists(self, bot_id: int, user_id: int) -> list[dict]:
        cursor = self._playlists.find({"bot_id": int(bot_id), "user_id": int(user_id)})
        out = []
        async for doc in cursor:
            out.append({"name": doc.get("name", ""), "tracks": doc.get("tracks") or []})
        return sorted(out, key=lambda d: d["name"])

    async def set_playlist_tracks(self, bot_id: int, user_id: int, name: str,
                                  tracks: list[dict]) -> dict:
        clean = [{k: t.get(k) for k in _TRACK_FIELDS} for t in (tracks or [])[:200]]
        await self._playlists.update_one(
            self._pkey(bot_id, user_id, name), {"$set": {"tracks": clean}}, upsert=True)
        return await self.get_playlist(bot_id, user_id, name) or {"name": name, "tracks": clean}

    async def delete_playlist(self, bot_id: int, user_id: int, name: str) -> bool:
        res = await self._playlists.delete_one(self._pkey(bot_id, user_id, name))
        return bool(getattr(res, "deleted_count", 1))

    @staticmethod
    def _key(bot_id: int, chat_id: int) -> dict:
        return {"bot_id": int(bot_id), "chat_id": int(chat_id)}

    async def get_settings(self, bot_id: int, chat_id: int) -> dict:
        doc = await self._settings.find_one(self._key(bot_id, chat_id))
        settings = default_settings()
        if doc:
            settings.update({k: v for k, v in doc.items() if k in settings})
        return settings

    async def update_settings(self, bot_id: int, chat_id: int, updates: dict) -> dict:
        allowed = {k: v for k, v in (updates or {}).items() if k in default_settings()}
        if allowed:
            await self._settings.update_one(
                self._key(bot_id, chat_id), {"$set": allowed}, upsert=True)
        return await self.get_settings(bot_id, chat_id)

    # ── persisted queues (survive worker restarts) ──────────────
    async def save_queue(self, bot_id: int, chat_id: int, tracks: list[dict]) -> None:
        clean = [{k: t.get(k) for k in _TRACK_FIELDS} for t in (tracks or [])[:100]]
        if clean:
            await self._queues.update_one(
                self._key(bot_id, chat_id), {"$set": {"tracks": clean}}, upsert=True)
        else:
            await self._queues.delete_one(self._key(bot_id, chat_id))

    async def load_queue(self, bot_id: int, chat_id: int) -> list[dict]:
        doc = await self._queues.find_one(self._key(bot_id, chat_id))
        if not doc:
            return []
        return [{k: t.get(k) for k in _TRACK_FIELDS}
                for t in doc.get("tracks") or [] if isinstance(t, dict)]
