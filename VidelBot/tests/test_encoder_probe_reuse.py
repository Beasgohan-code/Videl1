"""Offline regressions for reusing cached probe data in Encoder helpers."""
from types import SimpleNamespace

from tests.harness import load_all, run

load_all()

import VideoEncoder.utils.encoding as encoding  # noqa: E402
import VideoEncoder.utils.tasks as tasks  # noqa: E402


def test_extract_subs_uses_probe_summary(monkeypatch):
    calls = []

    async def fake_probe(filepath):
        calls.append(filepath)
        return {"subs": [{"codec_name": "subrip", "codec_tag_string": "[0][0][0][0]"}]}

    def fake_sync(filepath, output):
        calls.append(("sync", filepath, output))
        return output

    monkeypatch.setattr(encoding, "probe", fake_probe)
    monkeypatch.setattr(encoding, "_extract_subs_sync", fake_sync)
    msg = SimpleNamespace(id=123)
    out = run(encoding.extract_subs("in.mkv", msg, 5))
    assert out.endswith("123.ass")
    assert calls[0] == "in.mkv" and calls[1][0] == "sync"


def test_extract_subs_rejects_bitmap_first_subtitle(monkeypatch):
    async def fake_probe(filepath):
        return {"subs": [{"codec_name": "hdmv_pgs_subtitle"}]}

    def fail_sync(filepath, output):
        raise AssertionError("bitmap subtitles should not be extracted")

    monkeypatch.setattr(encoding, "probe", fake_probe)
    monkeypatch.setattr(encoding, "_extract_subs_sync", fail_sync)
    assert run(encoding.extract_subs("in.mkv", SimpleNamespace(id=1), 5)) is None


def test_extract_subs_returns_none_without_subtitles(monkeypatch):
    async def fake_probe(filepath):
        return {"subs": []}

    monkeypatch.setattr(encoding, "probe", fake_probe)
    assert run(encoding.extract_subs("in.mkv", SimpleNamespace(id=1), 5)) is None


def test_af_task_uses_cached_probe_audio_streams(monkeypatch):
    async def fake_download(message, msg):
        return "in.mkv"

    async def fake_probe(filepath):
        return {"audio": [{"index": 1, "codec_type": "audio", "tags": {}}]}

    class FakeStatus:
        async def delete(self):
            return None

    class FakeMessage:
        _client = object()
        from_user = SimpleNamespace(id=5)

        async def delete(self):
            return None

        async def reply(self, *a, **k):
            raise AssertionError("single-audio files should not open the selector")

    monkeypatch.setattr(tasks, "_download", fake_download)
    monkeypatch.setattr(tasks, "probe", fake_probe)
    run(tasks.af_task(FakeMessage(), FakeStatus()))
