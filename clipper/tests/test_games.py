"""The game filter: record only while the streamer is in an allowed Twitch category."""

import asyncio
import itertools
import json
import shutil

import pytest

from twitch_clipper.clipper import Clipper
from twitch_clipper.config import Settings
from twitch_clipper.recorder import Recorder, StreamInfo, game_allowed, parse_probe
from twitch_clipper.store import Store

GTA6 = "Grand Theft Auto VI"

FAKE_STREAM = [
    "ffmpeg", "-hide_banner", "-loglevel", "error", "-re",
    "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
    "-f", "lavfi", "-i", "sine=frequency=440",
    "-t", "60", "-c:v", "libx264", "-preset", "ultrafast", "-g", "30",
    "-c:a", "aac", "-f", "mpegts", "pipe:1",
]  # fmt: skip


def test_game_allowed_needs_exact_name():
    assert game_allowed(GTA6, [GTA6])
    assert game_allowed("grand theft auto vi", [GTA6])
    assert not game_allowed("Grand Theft Auto V", [GTA6])  # GTA 5 is not GTA 6
    assert not game_allowed("Just Chatting", [GTA6])
    assert not game_allowed("", [GTA6])
    assert game_allowed("Just Chatting", [])  # no list means any game


def test_parse_probe():
    live = {
        "plugin": "twitch",
        "metadata": {"id": "1", "author": "buster", "category": GTA6, "title": "первый день"},
        "streams": {"720p": {"type": "hls"}, "best": {"type": "hls"}},
    }
    assert parse_probe(json.dumps(live).encode()) == StreamInfo(GTA6, "первый день")
    offline = {"error": "No playable streams found on this URL: https://www.twitch.tv/buster"}
    assert parse_probe(json.dumps(offline).encode()) is None
    assert parse_probe(b"") is None
    assert parse_probe(b"not json") is None
    no_category = {"metadata": {"category": None, "title": None}, "streams": {"best": {}}}
    assert parse_probe(json.dumps(no_category).encode()) == StreamInfo("", "")


async def wait_for(condition, seconds: float = 15) -> bool:
    for _ in range(int(seconds / 0.1)):
        if condition():
            return True
        await asyncio.sleep(0.1)
    return False


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
async def test_recording_follows_the_game(tmp_path):
    cfg = Settings(
        data_dir=tmp_path,
        games=(GTA6,),
        segment_seconds=1,
        offline_poll_seconds=1,
        category_poll_seconds=1,
    )
    now_playing: list[StreamInfo | None] = [StreamInfo("Just Chatting", "болтаем")]

    async def probe() -> StreamInfo | None:
        return now_playing[0]

    rec = Recorder("buster", cfg, source_cmd=lambda: FAKE_STREAM, probe=probe)
    rec.start()
    try:
        assert await wait_for(lambda: rec.wrong_game)
        await asyncio.sleep(2.5)
        assert not rec.online and not list(rec.dir.glob("*.ts")), (
            "another game must not be recorded"
        )

        now_playing[0] = StreamInfo(GTA6, "наконец-то")
        assert await wait_for(lambda: rec.online), "recording must start once the game is GTA 6"
        assert not rec.wrong_game and rec.info == StreamInfo(GTA6, "наконец-то")

        now_playing[0] = StreamInfo("Chess", "перерыв")
        assert await wait_for(lambda: not rec.online), "recording must stop on another game"
        assert rec.wrong_game

        now_playing[0] = None  # went offline
        assert await wait_for(lambda: rec.info is None)
        assert not rec.wrong_game and not rec.online
    finally:
        await rec.stop()


async def test_statuses_for_the_list(tmp_path):
    cfg = Settings(data_dir=tmp_path, games=(GTA6,), offline_poll_seconds=1)
    store = Store(":memory:")
    for name in ("chatting", "offline"):
        store.add_streamer(name)
    playing = {"chatting": StreamInfo("Just Chatting", ""), "offline": None}

    def probe_factory(name):
        async def probe():
            return playing[name]

        return probe

    clipper = Clipper(cfg, store, probe_factory=probe_factory)
    await clipper.sync()
    try:
        assert await wait_for(lambda: clipper.recorders["chatting"].wrong_game)
        assert clipper.statuses() == {
            "chatting": ("other_game", "Just Chatting"),
            "offline": ("offline", ""),
        }
    finally:
        await clipper.close()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
async def test_tryout_reports_what_it_sees(tmp_path, capsys):
    from twitch_clipper import tryout

    cfg = Settings(data_dir=tmp_path, games=(GTA6,), segment_seconds=1, offline_poll_seconds=1)
    playing = {"gta": StreamInfo(GTA6, ""), "chat": StreamInfo("Just Chatting", ""), "off": None}

    def probe_factory(name):
        async def probe():
            return playing[name]

        return probe

    saved = await tryout.run(
        cfg,
        ["gta", "chat", "off"],
        minutes=0.2,
        source_factory=lambda name: lambda: FAKE_STREAM,
        probe_factory=probe_factory,
    )
    out = capsys.readouterr().out
    assert saved == []
    assert f"Игры: {GTA6}" in out
    assert f"gta              пишу · {GTA6} · чат: коплю статистику" in out
    assert "chat             в эфире, но другая игра: Just Chatting" in out
    assert "off              не в эфире" in out
    assert "Клипов сохранено: 0" in out


def test_unexpected_error_ignores_plain_offline():
    from twitch_clipper.recorder import unexpected_error

    offline = b'{"error": "No playable streams found on this URL: https://www.twitch.tv/x"}'
    assert unexpected_error(offline) == ""
    assert unexpected_error(b"error: No playable streams found on this URL: x\n") == ""
    assert unexpected_error(b"") == ""
    broken = b'{"error": "Unable to open URL: https://gql.twitch.tv/gql (403 Forbidden)"}'
    assert "403 Forbidden" in unexpected_error(broken)
    assert unexpected_error(b"warning: x\nerror: No module named streamlink\n") == (
        "error: No module named streamlink"
    )


async def test_many_streamers_are_checked_one_after_another(tmp_path):
    cfg = Settings(data_dir=tmp_path, games=(GTA6,), offline_poll_seconds=3)
    store = Store(":memory:")
    names = [f"streamer{i:02d}" for i in range(6)]
    for name in names:
        store.add_streamer(name)
    checked: list[tuple[str, float]] = []
    started = asyncio.get_running_loop().time()

    def probe_factory(name):
        async def probe():
            checked.append((name, asyncio.get_running_loop().time() - started))
            return None

        return probe

    clipper = Clipper(cfg, store, probe_factory=probe_factory)
    await clipper.sync()
    try:
        await asyncio.sleep(2.9)
    finally:
        await clipper.close()
    assert [name for name, _ in checked] == names  # each one once, in order
    times = [t for _, t in checked]
    assert times[0] < 0.2  # the first check is immediate
    gaps = [b - a for a, b in itertools.pairwise(times)]
    assert all(0.4 < gap < 0.7 for gap in gaps)  # 3 seconds / 6 streamers
