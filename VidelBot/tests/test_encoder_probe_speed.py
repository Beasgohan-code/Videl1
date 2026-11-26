"""Offline regressions for Encoder probe caching and single-probe upload metadata."""
import asyncio
import os

import pytest

from tests.harness import load_all, run

load_all()

import VideoEncoder.utils.encoding as encoding  # noqa: E402


@pytest.fixture(autouse=True)
def clear_probe_cache():
    encoding._PROBE_CACHE.clear()
    yield
    encoding._PROBE_CACHE.clear()


def fake_probe_json(duration="12.5", width=640, height=360):
    return {
        "streams": [{
            "codec_type": "video", "codec_name": "h264", "width": width, "height": height,
            "disposition": {}, "duration": duration,
        }],
        "format": {"duration": duration, "size": "12345"},
    }


def test_probe_caches_unchanged_file_and_returns_copies(tmp_path, monkeypatch):
    path = tmp_path / "clip.mkv"
    path.write_bytes(b"x" * 12345)
    calls = []

    def fake_sync(filepath):
        calls.append(filepath)
        return fake_probe_json()

    monkeypatch.setattr(encoding, "_probe_sync", fake_sync)
    first = run(encoding.probe(str(path)))
    first["video"]["width"] = 9999
    second = run(encoding.probe(str(path)))
    assert len(calls) == 1
    assert second["width"] == 640 and second["video"]["width"] == 640


def test_probe_cache_invalidates_when_file_changes(tmp_path, monkeypatch):
    path = tmp_path / "clip.mkv"
    path.write_bytes(b"x" * 10)
    calls = []

    def fake_sync(filepath):
        calls.append(filepath)
        return fake_probe_json(duration=str(len(calls)))

    monkeypatch.setattr(encoding, "_probe_sync", fake_sync)
    assert run(encoding.probe(str(path)))["duration"] == 1.0
    path.write_bytes(b"x" * 11)
    os.utime(path, (1000000000, 1000000000))
    assert run(encoding.probe(str(path)))["duration"] == 2.0
    assert len(calls) == 2


def test_failed_probe_is_not_cached(tmp_path, monkeypatch):
    path = tmp_path / "bad.mkv"
    path.write_bytes(b"x")
    calls = []

    def fake_sync(filepath):
        calls.append(filepath)
        return {}

    monkeypatch.setattr(encoding, "_probe_sync", fake_sync)
    assert run(encoding.probe(str(path)))["ok"] is False
    assert run(encoding.probe(str(path)))["ok"] is False
    assert len(calls) == 2


def test_probe_many_preserves_order(monkeypatch):
    async def fake_probe(path):
        await asyncio.sleep(0.01 if path == "a" else 0)
        return {"path": path}

    monkeypatch.setattr(encoding, "probe", fake_probe)
    assert run(encoding.probe_many(["a", "b", "c"])) == [{"path": "a"}, {"path": "b"}, {"path": "c"}]


def test_media_metadata_uses_one_probe_and_fallbacks(monkeypatch):
    calls = []

    async def fake_probe(path):
        calls.append(path)
        return {"duration": 12.9, "width": 0, "height": 0}

    monkeypatch.setattr(encoding, "probe", fake_probe)
    assert run(encoding.media_metadata("clip.mkv")) == (12, 1280, 720)
    assert calls == ["clip.mkv"]
