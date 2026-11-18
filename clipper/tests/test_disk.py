"""What is kept on disk and when it is deleted."""

import os
import time

from twitch_clipper.clipper import Clipper
from twitch_clipper.config import Settings, load_dotenv
from twitch_clipper.recorder import Recorder
from twitch_clipper.store import Store
from twitch_clipper.text import build_caption


def touch(path, size=10, age=0.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))
    return path


def clip_with_source(store, folder, clip_id_hint, size=10, age=0.0):
    clip = store.add_clip("buster", 3.0, None, None, "other", "")
    path = touch(folder / "sources" / f"{clip}.ts", size, age)
    store.set_source(clip, path, 0.0, 100.0)
    if age:
        store.db.execute("UPDATE clips SET created_at = ? WHERE id = ?", (time.time() - age, clip))
        store.db.commit()
    return clip, path


def test_start_removes_leftovers_of_a_previous_run(tmp_path):
    store = Store(":memory:")
    _, kept = clip_with_source(store, tmp_path, 1)
    orphan = touch(tmp_path / "sources" / "999.ts")
    old_segment = touch(tmp_path / "buffer" / "buster" / "1_000001.ts")
    half_made = touch(tmp_path / "clips" / "buster_1.mp4")
    database = touch(tmp_path / "clipper.db")

    Clipper(Settings(data_dir=tmp_path), store).clean_leftovers()

    assert kept.exists() and database.exists()
    assert not orphan.exists() and not old_segment.exists() and not half_made.exists()


def test_sources_are_deleted_by_age(tmp_path):
    store = Store(":memory:")
    fresh, fresh_path = clip_with_source(store, tmp_path, 1, age=3600)
    old, old_path = clip_with_source(store, tmp_path, 2, age=25 * 3600)

    Clipper(Settings(data_dir=tmp_path, keep_source_hours=24), store).clean_sources()

    assert fresh_path.exists() and store.get_clip(fresh).source_path
    assert not old_path.exists() and store.get_clip(old).source_path == ""


def test_sources_are_capped_by_total_size_oldest_first(tmp_path):
    store = Store(":memory:")
    megabyte = 1024 * 1024
    clips = [clip_with_source(store, tmp_path, i, size=megabyte, age=age) for i, age in
             enumerate((300, 200, 100))]  # fmt: skip
    cfg = Settings(data_dir=tmp_path, sources_max_gb=2.5 / 1024)  # room for two of the three

    Clipper(cfg, store).clean_sources()

    (oldest, oldest_path), (_, middle_path), (_, newest_path) = clips
    assert not oldest_path.exists() and store.get_clip(oldest).source_path == ""
    assert middle_path.exists() and newest_path.exists()


async def test_stopping_a_recorder_drops_its_buffer(tmp_path):
    rec = Recorder("buster", Settings(data_dir=tmp_path))
    touch(rec.dir / "1_000000.ts")
    await rec.stop()
    assert not rec.dir.exists()


def test_segments_follow_file_names_not_modification_times(tmp_path):
    rec = Recorder("buster", Settings(data_dir=tmp_path, segment_seconds=4))
    # The file being written reports a stale time, older than the finished one before it.
    touch(rec.dir / "1700000000_000000.ts", age=12)
    touch(rec.dir / "1700000000_000001.ts", age=8)
    touch(rec.dir / "1700000000_000002.ts", age=4)
    touch(rec.dir / "1700000000_000003.ts", age=6)  # in progress
    rec._recording = True
    assert [s.path.name[-9:-3] for s in rec.segments()] == ["000000", "000001", "000002"]
    # A restart of the recorder gets a later prefix and sorts after the earlier run.
    touch(rec.dir / "1700000100_000000.ts", age=1)
    assert rec.segments()[-1].path.name == "1700000000_000003.ts"


def test_caption_survives_a_missing_time_zone_database():
    caption = build_caption("x", 1795001700, "No/Such_Zone", "other", 3.0, None, None, "", [])
    assert caption.startswith("🎬 x · ")


def test_dotenv_written_by_notepad(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_bytes("﻿CLIPPER_TEST_TOKEN=123:abc\n# comment\nCLIPPER_TEST_Q='q'\n".encode())
    monkeypatch.delenv("CLIPPER_TEST_TOKEN", raising=False)
    monkeypatch.delenv("CLIPPER_TEST_Q", raising=False)
    load_dotenv(env)
    assert os.environ.pop("CLIPPER_TEST_TOKEN") == "123:abc"
    assert os.environ.pop("CLIPPER_TEST_Q") == "q"
