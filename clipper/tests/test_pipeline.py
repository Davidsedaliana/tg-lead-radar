"""End-to-end run on a synthetic stream: record -> chat spike -> cut -> render -> send."""

import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from twitch_clipper.chat import ChatMessage
from twitch_clipper.clipper import Clipper
from twitch_clipper.config import Settings
from twitch_clipper.judge import Verdict
from twitch_clipper.store import Store

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")

# A fake "stream": test picture in real time, the sound gets loud after 10 seconds.
FAKE_STREAM = [
    "ffmpeg", "-hide_banner", "-loglevel", "error", "-re",
    "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
    "-af", "volume='if(gt(t,10),1,0.03)':eval=frame",
    "-t", "60", "-c:v", "libx264", "-preset", "ultrafast", "-g", "30",
    "-c:a", "aac", "-f", "mpegts", "pipe:1",
]  # fmt: skip


class FakeJudge:
    def __init__(self, score: int) -> None:
        self.score = score
        self.calls: list[dict] = []

    model = "fake"

    async def rate(self, moment):
        self.calls.append({"chat": moment.chat_lines, "frames": moment.frames})
        return Verdict(self.score, "funny", "тестовый момент")


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-of", "json", "-show_entries",
         "stream=codec_type,width,height:format=duration", str(path)],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    return json.loads(out)


async def run_scenario(tmp_path: Path, judge: FakeJudge | None, vertical: bool = True):
    cfg = Settings(
        data_dir=tmp_path / "data",
        segment_seconds=2,
        pre_seconds=6,
        post_seconds=3,
        chat_window=3,
        chat_min_messages=10,
        chat_warmup_seconds=4,
        vertical=vertical,
    )
    store = Store(":memory:")
    store.add_streamer("teststreamer")
    sent: list[tuple[Path, str, int]] = []

    async def send_clip(path: Path, caption: str, clip_id: int, is_vertical: bool) -> None:
        kept = tmp_path / path.name
        shutil.copy(path, kept)
        sent.append((kept, caption, clip_id))

    clipper = Clipper(
        cfg,
        store,
        send_clip=send_clip,
        judge=judge,
        source_factory=lambda name: lambda: FAKE_STREAM,
    )
    await clipper.sync()  # starts the recorder; the chat connection is not opened
    rec = clipper.recorders["teststreamer"]
    try:
        for _ in range(100):
            if rec.online:
                break
            await asyncio.sleep(0.2)
        assert rec.online, "recorder did not start producing segments"
        assert clipper.online() == {"teststreamer"}

        def say(text: str) -> None:
            clipper.on_message(ChatMessage("teststreamer", "viewer", text, time.time()))

        for i in range(12):  # the usual pace: one message per second
            say(f"обычное сообщение {i}")
            await asyncio.sleep(1)
        for _ in range(30):  # the moment
            say("АХАХАХА")
            await asyncio.sleep(0.02)
        say("что это было")

        # after the spike: 3s of "after", 2 closed segments, then render
        for _ in range(250):
            if sent or (judge and judge.calls and judge.score < cfg.min_score):
                break
            await asyncio.sleep(0.2)
        await asyncio.sleep(0.5)
    finally:
        await clipper.close()
    leftovers = list((cfg.data_dir / "clips").glob("*"))
    return cfg, sent, leftovers


async def test_spike_produces_vertical_clip(tmp_path):
    judge = FakeJudge(score=8)
    cfg, sent, leftovers = await run_scenario(tmp_path, judge)

    assert len(sent) == 1
    clip, caption, clip_id = sent[0]
    info = probe(clip)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (720, 1280)
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    assert 6.0 <= float(info["format"]["duration"]) <= 10.5  # 6s before + 3s after

    lines = caption.splitlines()
    assert lines[0].startswith("🎬 teststreamer · ")
    assert "😂 смешно · чат x" in lines[0]
    assert "🔊 x" in lines[0]
    assert lines[1] == "🤖 8/10: тестовый момент"
    assert "💬 АХАХАХА" in lines

    assert len(judge.calls) == 1
    assert len(judge.calls[0]["frames"]) == 3
    assert all(f[:2] == b"\xff\xd8" for f in judge.calls[0]["frames"])  # JPEG
    assert "АХАХАХА" in judge.calls[0]["chat"]
    assert clip_id == 1
    assert leftovers == []  # temporary files are removed


async def test_low_score_is_not_sent(tmp_path):
    judge = FakeJudge(score=3)
    _, sent, leftovers = await run_scenario(tmp_path, judge)
    assert len(judge.calls) == 1
    assert sent == []
    assert leftovers == []


async def test_without_judge_clip_is_sent_unscored_horizontal(tmp_path):
    _, sent, _ = await run_scenario(tmp_path, judge=None, vertical=False)
    assert len(sent) == 1
    clip, caption, _ = sent[0]
    video = next(s for s in probe(clip)["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1280, 720)
    assert "🤖" not in caption
    assert "😂 смешно" in caption  # guessed from chat keywords
