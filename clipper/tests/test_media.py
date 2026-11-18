import json
import shutil
import subprocess

import pytest

from twitch_clipper.media import grab_frames, mean_volume, render, split_jpegs

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


@pytest.fixture
def raw(tmp_path):
    """Six seconds of MPEG-TS with a single keyframe at the very start (the worst case)."""
    path = tmp_path / "raw.ts"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
         "-f", "lavfi", "-i", "sine=frequency=440",
         "-t", "6", "-c:v", "libx264", "-preset", "ultrafast", "-g", "600",
         "-c:a", "aac", "-f", "mpegts", str(path)],
        check=True,
    )  # fmt: skip
    return path


def probe(path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-of", "json", "-count_frames", "-show_entries",
         "stream=codec_type,width,height,nb_read_frames:format=duration", str(path)],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    return json.loads(out)


@pytest.mark.parametrize(("vertical", "size"), [(True, (720, 1280)), (False, (1280, 720))])
async def test_render_keeps_video_when_cut_starts_between_keyframes(raw, tmp_path, vertical, size):
    out = tmp_path / "out.mp4"
    await render(raw, out, offset=1.5, duration=4, vertical=vertical)
    info = probe(out)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == size
    assert 110 <= int(video["nb_read_frames"]) <= 125  # 4 seconds at 30 fps
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    assert 3.8 <= float(info["format"]["duration"]) <= 4.3


async def test_grab_frames_returns_evenly_spread_jpegs(raw):
    frames = await grab_frames(raw, offset=1.5, duration=4, count=3)
    assert len(frames) == 3
    assert all(f.startswith(b"\xff\xd8") and f.endswith(b"\xff\xd9") for f in frames)
    assert len(set(frames)) == 3  # three different moments


async def test_mean_volume(raw, tmp_path):
    level = await mean_volume(raw)
    assert level is not None and -40 < level < 0
    assert await mean_volume(tmp_path / "missing.ts") is None


def test_split_jpegs():
    a, b = b"\xff\xd8aaa\xff\xd9", b"\xff\xd8bb\xff\xd9"
    assert split_jpegs(a + b) == [a, b]
    assert split_jpegs(b"") == []
