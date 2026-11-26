"""Profile routing and desired-state controls shared by clone runtimes."""
from datetime import datetime, timedelta, timezone

import pytest

from tests.harness import run

from filestore.database.main_db import MainDB


async def _add(bot_id: int, owner_id: int = 5):
    return await MainDB().add_bot(bot_id, owner_id, "enc", f"Clone{bot_id}", -100123)


def test_active_clone_queries_partition_by_runtime():
    async def scenario():
        db = MainDB()
        ids = (927161728100, 927161728101, 927161728102, 927161728103)
        for bot_id in ids:
            await db.bots.delete_one({"_id": bot_id})
        await _add(ids[0])  # legacy = File Store
        await _add(ids[1])
        await _add(ids[2])
        await _add(ids[3])
        await db.bots.update_one({"_id": ids[1]}, {"$set": {"clone_profile": "filestore"}})
        await db.bots.update_one({"_id": ids[2]}, {"$set": {"clone_profile": "group"}})
        await db.bots.update_one({"_id": ids[3]}, {"$set": {"clone_profile": "music"}})
        file_store = await db.get_all_active_bots()
        group = await db.get_active_bots_for_profile("group")
        music = await db.get_active_bots_for_profile("music")
        assert {doc["_id"] for doc in file_store} == {ids[0], ids[1]}
        assert {doc["_id"] for doc in group} == {ids[2]}
        assert {doc["_id"] for doc in music} == {ids[3]}
        with pytest.raises(ValueError):
            await db.get_active_bots_for_profile("invalid")
    run(scenario())


def test_music_desired_state_requires_owner_and_recovery_requires_stopped():
    async def scenario():
        db = MainDB()
        bot_id = 927161728104
        await db.bots.delete_one({"_id": bot_id})
        await _add(bot_id)
        await db.bots.update_one({"_id": bot_id}, {"$set": {"clone_profile": "music"}})
        assert not await db.set_music_session(bot_id, 999, "enc:wrong-owner-session")
        assert "music_session_encrypted" not in await db.get_bot(bot_id)
        assert await db.set_music_session(bot_id, 5, "enc:owner-session")
        assert not await db.update_music_settings_defaults(bot_id, 999, {"autoplay": True})
        assert not await db.update_music_settings_defaults(bot_id, 5, {"volume": True})
        assert await db.update_music_settings_defaults(bot_id, 5,
                                                       {"autoplay": True, "speed": 1.25})
        assert not await db.set_profile_active(bot_id, 999, "music", False)
        assert await db.set_profile_active(bot_id, 5, "music", False)
        await db.set_music_runtime_state(bot_id, "running")
        assert not await db.recover_music_profile(bot_id, 5, "filestore")
        # If the separate Music worker dies, its last active report must not
        # block an owner-confirmed recovery forever. Missing timestamps remain
        # conservative and do not qualify as stale.
        await db.bots.update_one({"_id": bot_id}, {"$unset": {"music_runtime_updated_at": ""}})
        assert not await db.recover_music_profile(bot_id, 5, "filestore")
        old = datetime.now(timezone.utc) - timedelta(seconds=180)
        await db.bots.update_one({"_id": bot_id}, {"$set": {"music_runtime_updated_at": old}})
        assert await db.recover_music_profile(bot_id, 5, "filestore")
        saved = await db.get_bot(bot_id)
        assert saved["clone_profile"] == "filestore"
        assert saved["is_active"] is False
        assert "music_runtime_state" not in saved
    run(scenario())
