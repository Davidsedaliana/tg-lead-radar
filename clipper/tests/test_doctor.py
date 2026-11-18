"""The self-check command: what it reports when things work and when they do not."""

import re
import shutil

import pytest

from twitch_clipper import doctor
from twitch_clipper.config import Settings
from twitch_clipper.recorder import StreamInfo

FAKE_STREAM = [
    "ffmpeg", "-hide_banner", "-loglevel", "error", "-re",
    "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
    "-f", "lavfi", "-i", "sine=frequency=440",
    "-t", "30", "-c:v", "libx264", "-preset", "ultrafast", "-g", "30",
    "-c:a", "aac", "-f", "mpegts", "pipe:1",
]  # fmt: skip

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


async def test_find_live_reports_who_is_on_air():
    playing = {"a": None, "b": StreamInfo("Grand Theft Auto VI", ""), "c": StreamInfo("", "")}

    async def probe(name):
        return playing[name]

    detail, live = await doctor.find_live(["a", "b", "c"], probe)
    assert live == "b"
    assert detail == "в эфире 2 из 3: b (Grand Theft Auto VI), c (без категории)"

    detail, live = await doctor.find_live(["a"], probe)
    assert live == "" and "никто не в эфире" in detail


async def test_find_live_fails_when_twitch_is_unreachable():
    async def probe(name):
        raise RuntimeError("Unable to open URL")

    with pytest.raises(RuntimeError, match="Twitch не отвечает: Unable to open URL"):
        await doctor.find_live(["a", "b"], probe)


@needs_ffmpeg
async def test_local_tools_are_found():
    assert await doctor.check_python()
    assert await doctor.check_ffmpeg()
    assert await doctor.check_streamlink()


@needs_ffmpeg
async def test_recording_check_builds_a_clip(tmp_path):
    cfg = Settings(data_dir=tmp_path, segment_seconds=1)
    detail = await doctor.check_recording(cfg, "buster", source_cmd=lambda: FAKE_STREAM, wait=20)
    assert re.fullmatch(r"buster: записал [3-9] кусков, собрал клип на \d+ КБ", detail)


@needs_ffmpeg
async def test_recording_check_fails_without_video(tmp_path):
    cfg = Settings(data_dir=tmp_path, segment_seconds=1, offline_poll_seconds=1)
    nothing = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-version"]
    with pytest.raises(RuntimeError, match="не записалось"):
        await doctor.check_recording(cfg, "buster", source_cmd=lambda: nothing, wait=2)


async def test_skipped_checks_without_keys():
    with pytest.raises(doctor.Skip, match="BOT_TOKEN"):
        await doctor.check_telegram(Settings())
    with pytest.raises(doctor.Skip, match="ключ API"):
        await doctor.check_judge(Settings(), {})


async def test_report_counts_failures(capsys, monkeypatch):
    async def ok():
        return "1.0"

    async def chat_down(channel):
        raise ConnectionError("нет сети")

    async def probe(name):
        return None

    for name in ("check_python", "check_ffmpeg", "check_streamlink"):
        monkeypatch.setattr(doctor, name, ok)
    monkeypatch.setattr(doctor, "check_chat", chat_down)

    assert await doctor.run(Settings(), ["buster"], {}, probe=probe) is False
    out = capsys.readouterr().out
    assert "[  OK  ] ffmpeg: 1.0" in out
    assert "[  OK  ] Twitch, эфиры: Twitch отвечает, но из 1 проверенных никто не в эфире" in out
    assert "[ОШИБКА] Twitch, чат: ConnectionError: нет сети" in out
    assert "[  --  ] Запись и сборка клипа" in out
    assert "[  --  ] Telegram: BOT_TOKEN не задан" in out
    assert "[  --  ] Нейросеть: ключ API не задан" in out
    assert "Не работает пунктов: 1." in out
