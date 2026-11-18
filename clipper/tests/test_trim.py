"""Trim buttons: the rules for moving clip boundaries, storage, and the bot handler."""

import contextlib
import sqlite3
import time

import pytest
from aiogram.methods import AnswerCallbackQuery, EditMessageMedia, SendMessage
from test_bot import CHAT, Harness

from twitch_clipper.clipper import Clipper, TrimError, TrimPlan
from twitch_clipper.config import Settings
from twitch_clipper.store import Store


def make_clipper(tmp_path, start=1000.0, end=1045.0, source=(970.0, 1075.0)):
    """A clipper with one stored clip of 45 seconds and 30 seconds of margin on each side."""
    cfg = Settings(data_dir=tmp_path)
    store = Store(":memory:")
    clip_id = store.add_clip("buster", 3.0, None, 8, "funny", "смешно")
    store.set_clip_range(clip_id, start, end, "подпись")
    if source is not None:
        path = tmp_path / "source.ts"
        path.write_bytes(b"video")
        store.set_source(clip_id, path, *source)
    return Clipper(cfg, store), store, clip_id


def test_each_button_moves_one_boundary(tmp_path):
    clipper, _, clip = make_clipper(tmp_path)
    moved = {a: clipper.plan_trim(clip, a) for a in ("s-10", "s+5", "e-5", "e+10")}
    assert (moved["s-10"].start, moved["s-10"].end) == (990.0, 1045.0)
    assert (moved["s+5"].start, moved["s+5"].end) == (1005.0, 1045.0)
    assert (moved["e-5"].start, moved["e-5"].end) == (1000.0, 1040.0)
    assert (moved["e+10"].start, moved["e+10"].end) == (1000.0, 1055.0)
    assert moved["e+10"].duration == 55.0
    assert moved["e+10"].caption == "подпись" and moved["e+10"].source_start == 970.0


def test_cannot_go_past_the_saved_source(tmp_path):
    clipper, store, clip = make_clipper(tmp_path, source=(995.0, 1045.0))
    assert clipper.plan_trim(clip, "s-10").start == 995.0  # only 5 of the 10 seconds exist
    with pytest.raises(TrimError, match="записи нет"):
        clipper.plan_trim(clip, "e+10")
    store.set_clip_range(clip, 995.0, 1045.0)
    with pytest.raises(TrimError, match="записи нет"):
        clipper.plan_trim(clip, "s-10")


def test_cannot_get_shorter_than_five_seconds(tmp_path):
    clipper, _, clip = make_clipper(tmp_path, start=1000.0, end=1008.0)
    with pytest.raises(TrimError, match="Короче"):
        clipper.plan_trim(clip, "e-5")
    with pytest.raises(TrimError, match="Короче"):
        clipper.plan_trim(clip, "s+5")


def test_reasons_a_clip_cannot_be_trimmed(tmp_path):
    clipper, store, clip = make_clipper(tmp_path)
    with pytest.raises(TrimError, match="нельзя"):
        clipper.plan_trim(999, "s-10")
    with pytest.raises(TrimError, match="нельзя"):
        clipper.plan_trim(clip, "bogus")

    (tmp_path / "source.ts").unlink()
    with pytest.raises(TrimError, match="удалён"):
        clipper.plan_trim(clip, "s-10")

    now = time.time()
    fresh, _, fresh_clip = make_clipper(tmp_path, start=now - 45, end=now, source=None)
    with pytest.raises(TrimError, match="дописывается"):
        fresh.plan_trim(fresh_clip, "s-10")
    old, _, old_clip = make_clipper(tmp_path, source=None)
    with pytest.raises(TrimError, match="не сохранился"):
        old.plan_trim(old_clip, "s-10")


def test_store_keeps_ranges_and_expires_sources(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    clip = store.add_clip("buster", 3.0, None, None, "other", "")
    assert store.get_clip(clip).start is None and store.get_clip(clip).source_path == ""
    store.set_clip_range(clip, 10.0, 55.0, "подпись")
    store.set_clip_range(clip, 5.0, 55.0)  # a re-cut keeps the caption
    store.set_source(clip, tmp_path / "1.ts", 0.0, 80.0)
    row = store.get_clip(clip)
    assert (row.start, row.end, row.caption) == (5.0, 55.0, "подпись")
    assert (row.source_path, row.source_start, row.source_end) == (str(tmp_path / "1.ts"), 0, 80)

    assert store.pop_old_sources(time.time() - 3600) == []  # the clip is fresh
    assert store.pop_old_sources(time.time() + 1) == [str(tmp_path / "1.ts")]
    assert store.get_clip(clip).source_path == ""
    assert store.pop_old_sources(time.time() + 1) == []
    assert store.get_clip(999) is None


def test_store_upgrades_a_database_from_the_first_version(tmp_path):
    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE clips (id INTEGER PRIMARY KEY AUTOINCREMENT, streamer TEXT NOT NULL,"
        " created_at REAL NOT NULL, chat_mult REAL, audio_mult REAL, score INTEGER,"
        " category TEXT, summary TEXT)"
    )
    db.execute("INSERT INTO clips (streamer, created_at) VALUES ('buster', 1)")
    db.commit()
    db.close()

    store = Store(path)
    assert store.get_clip(1).streamer == "buster" and store.get_clip(1).start is None
    store.set_clip_range(1, 1.0, 2.0, "c")
    assert Store(path).get_clip(1).caption == "c"  # opening it again changes nothing


class FakeTrimmer:
    def __init__(self, tmp_path) -> None:
        self.tmp_path = tmp_path
        self.error: str | None = None
        self.planned: list[tuple[int, str]] = []
        self.finished = 0

    def plan_trim(self, clip_id: int, action: str) -> TrimPlan:
        self.planned.append((clip_id, action))
        if self.error:
            raise TrimError(self.error)
        return TrimPlan(clip_id, self.tmp_path / "s.ts", 0.0, 10.0, 65.0, "подпись клипа")

    @contextlib.asynccontextmanager
    async def trimmed(self, plan: TrimPlan):
        path = self.tmp_path / "recut.mp4"
        path.write_bytes(b"mp4")
        yield path
        self.finished += 1


def harness_with_trimmer(tmp_path) -> tuple[Harness, FakeTrimmer]:
    h = Harness()
    trimmer = FakeTrimmer(tmp_path)
    h.clipbot._trimmer = trimmer
    return h, trimmer


async def test_trim_button_replaces_the_video_in_place(tmp_path):
    h, trimmer = harness_with_trimmer(tmp_path)
    clip = h.store.add_clip("buster", 3.0, None, 8, "funny", "x")
    h.store.vote(clip, 1, 1)

    answer, edit = await h.press(f"x:{clip}:s-10")

    assert trimmer.planned == [(clip, "s-10")] and trimmer.finished == 1
    assert isinstance(answer, AnswerCallbackQuery) and "будет 55 с" in answer.text
    assert isinstance(edit, EditMessageMedia)
    assert edit.chat_id == CHAT and edit.media.caption == "подпись клипа"
    assert (edit.media.width, edit.media.height) == (720, 1280)
    rows = edit.reply_markup.inline_keyboard
    assert rows[0][0].text == "👍 1"  # votes survive the re-cut
    assert rows[1][0].callback_data == f"x:{clip}:s-10"


async def test_trim_button_explains_why_it_cannot(tmp_path):
    h, trimmer = harness_with_trimmer(tmp_path)
    trimmer.error = "Дальше в эту сторону записи нет"
    [answer] = await h.press("x:5:e+10")
    assert answer.text == "Дальше в эту сторону записи нет" and answer.show_alert is True
    assert trimmer.finished == 0


async def test_trim_failure_is_reported_in_chat(tmp_path):
    h, trimmer = harness_with_trimmer(tmp_path)

    @contextlib.asynccontextmanager
    async def broken(plan):
        raise RuntimeError("ffmpeg died")
        yield

    trimmer.trimmed = broken
    answer, message = await h.press("x:5:e-5")
    assert isinstance(answer, AnswerCallbackQuery)
    assert isinstance(message, SendMessage) and "Не получилось" in message.text


async def test_trim_buttons_ignored_outside_the_chat_and_when_disabled(tmp_path):
    h, trimmer = harness_with_trimmer(tmp_path)
    assert await h.press("x:5:s-10", chat_id=999) == []
    assert trimmer.planned == []

    h.clipbot._trimmer = None
    [answer] = await h.press("x:5:s-10")
    assert answer.text == "Подрезка выключена"
