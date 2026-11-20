"""Offline regressions for single-process screenshot capture."""
from tests.harness import load_all, run

load_all()

import VideoEncoder.utils.encoding as encoding  # noqa: E402
from VideoEncoder.utils import ffcmd  # noqa: E402


def test_screenshots_command_maps_each_timestamp_input():
    cmd = ffcmd.screenshots_command("in.mkv", ["a.jpg", "b.jpg"], [1.5, 4.25])
    assert cmd[0] == "ffmpeg"
    assert cmd.count("-ss") == 2 and cmd.count("-i") == 2
    assert "-map" in cmd and "0:v" in cmd and "1:v" in cmd
    assert cmd[-4:] == ["-q:v", "3", "b.jpg"] or cmd[-1] == "b.jpg"
    assert "a.jpg" in cmd and "b.jpg" in cmd


def test_screenshots_command_rejects_mismatched_outputs():
    try:
        ffcmd.screenshots_command("in.mkv", ["a.jpg"], [1.0, 2.0])
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_screenshots_uses_one_batch_process_when_all_outputs_exist(tmp_path, monkeypatch):
    calls = []

    async def fake_run(cmd, key=None, timeout=None):
        calls.append(cmd)
        for out in [arg for arg in cmd if str(arg).endswith(".jpg")]:
            open(out, "wb").write(b"jpeg")
        return 0, ""

    monkeypatch.setattr(encoding, "run_ffmpeg", fake_run)
    shots = run(encoding.screenshots("in.mkv", 2, duration=10, out_dir=str(tmp_path)))
    assert len(calls) == 1
    assert [at for _, at in shots] == ffcmd.screenshot_times(10, 2)
    assert all(any(p.name.endswith(f"_0{i}.jpg") for p in tmp_path.iterdir()) for i in (1, 2))


def test_screenshots_falls_back_only_for_missing_frames(tmp_path, monkeypatch):
    calls = []

    async def fake_run(cmd, key=None, timeout=None):
        calls.append(cmd)
        jpgs = [arg for arg in cmd if str(arg).endswith(".jpg")]
        if len(calls) == 1:
            open(jpgs[0], "wb").write(b"jpeg")
            return 1, "batch failed"
        open(jpgs[-1], "wb").write(b"jpeg")
        return 0, ""

    monkeypatch.setattr(encoding, "run_ffmpeg", fake_run)
    shots = run(encoding.screenshots("in.mkv", 2, duration=10, out_dir=str(tmp_path)))
    assert len(calls) == 2
    assert [out.rsplit("/", 1)[-1][-7:] for out, _ in shots] == ["_01.jpg", "_02.jpg"]
