import dataclasses
import sqlite3
from datetime import UTC, datetime

import pytest

from tg_lead_radar.jobs.config import load_jobs_config
from tg_lead_radar.jobs.dedup import fingerprint, normalize
from tg_lead_radar.jobs.store import JobsStore, Source, to_iso

CFG = load_jobs_config().dedup


@pytest.fixture
def store(tmp_path):
    s = JobsStore(str(tmp_path / "jobs.db"))
    yield s
    s.close()


def src(chat_id, msg_id, kind="post", day=15, views=100, channel="qa_jobs"):
    return Source(
        chat_id=chat_id, msg_id=msg_id, kind=kind, channel=channel,
        channel_title=channel.upper() if channel else None,
        message_link=f"https://t.me/{channel}/{msg_id}" if channel else None,
        posted_at=f"2026-10-{day:02d}T12:00:00+03:00", views=views,
    )


def add(store, vid, text, sources, **fields):
    record = {"text": text, "matched_stems": ["qa"], **fields}
    store.add_vacancy(vid, fingerprint(text, fields, CFG), normalize(text), record, sources)


def test_to_iso_is_moscow_time():
    assert to_iso(datetime(2026, 10, 15, 21, 30, tzinfo=UTC)) == "2026-10-16T00:30:00+03:00"


class TestChannels:
    def test_last_msg_id_never_goes_back(self, store):
        assert store.last_msg_id(1) is None
        store.set_last_msg_id(1, "qa_jobs", "QA", 50)
        store.set_last_msg_id(1, "qa_jobs", "QA", 40)
        assert store.last_msg_id(1) == 50


class TestVacancies:
    def test_add_and_lookup(self, store):
        add(store, "v1", "QA Engineer", [src(1, 10)])
        assert store.has_post(1, 10)
        assert not store.has_post(1, 11)
        assert store.find_by_hash(fingerprint("QA Engineer", {}, CFG).text_hash) == "v1"
        [(vid, fp)] = store.candidates("2026-10-01")
        assert vid == "v1" and fp.title_key == "qa engineer"

    def test_candidates_respect_the_window(self, store):
        add(store, "old", "QA old", [src(1, 1, day=1)])
        add(store, "new", "QA new", [src(1, 2, day=15)])
        assert [vid for vid, _ in store.candidates("2026-10-10")] == ["new"]

    def test_attach_fills_missing_fields_and_keeps_all_sources(self, store):
        add(store, "v1", "QA Engineer", [src(1, 10, day=15)], salary_from=None, city="Москва")
        rev = store.rev()
        store.attach(
            "v1", {"salary_from": 200_000, "city": "Казань"}, ["pytest"],
            [src(2, 5, day=14, channel="it_remote")],
        )
        [rec] = store.records()
        assert rec["salary_from"] == 200_000
        assert rec["city"] == "Москва"  # the first copy's value stays
        assert rec["matched_stems"] == ["qa", "pytest"]
        assert [s["channel"] for s in rec["sources"]] == ["it_remote", "qa_jobs"]
        # the earliest post is the primary source
        assert rec["channel"] == "it_remote"
        assert rec["posted_at"] == "2026-10-14T12:00:00+03:00"
        assert store.rev() == rev + 1

    def test_origin_becomes_a_post_when_read_in_its_own_channel(self, store):
        add(store, "v1", "QA", [src(1, 10, day=15), src(9, 3, kind="forward", day=12, views=None)])
        [rec] = store.records()
        assert rec["channel"] == "qa_jobs"  # forwards never become the primary source
        store.attach("v1", {}, [], [src(9, 3, day=12, views=777, channel="origin")])
        [rec] = store.records()
        assert rec["channel"] == "origin" and rec["views"] == 777
        assert {s["kind"] for s in rec["sources"]} == {"post"}

    def test_private_channel_is_named_by_id(self, store):
        add(store, "v1", "QA", [src(123, 1, channel=None)])
        assert store.records()[0]["channel"] == "c/123"


class TestRecords:
    def test_record_shape(self, store):
        add(store, "v1", "QA Engineer", [src(1, 10)], grade=["senior"], links=["https://x.io"])
        [rec] = store.records()
        assert list(rec)[:7] == [
            "id", "posted_at", "channel", "channel_title", "message_link", "views", "text",
        ]
        assert rec["grade"] == ["senior"]
        assert rec["links"] == ["https://x.io"]
        assert rec["attachments"] == [] and rec["original"] is None
        assert rec["sources"][0] == {
            "kind": "post", "channel": "qa_jobs", "channel_title": "QA_JOBS",
            "message_link": "https://t.me/qa_jobs/10",
            "posted_at": "2026-10-15T12:00:00+03:00", "views": 100,
        }

    def test_changed_since_a_revision(self, store):
        add(store, "v1", "QA one", [src(1, 1)])
        mark = store.rev()
        add(store, "v2", "QA two", [src(1, 2)])
        assert [r["id"] for r in store.records(after_rev=mark)] == ["v2"]
        store.attach("v1", {}, [], [src(2, 1)])
        assert [r["id"] for r in store.records(after_rev=mark)] == ["v1", "v2"]

    def test_by_dates(self, store):
        add(store, "v1", "QA one", [src(1, 1, day=14)])
        add(store, "v2", "QA two", [src(1, 2, day=15)])
        assert [r["id"] for r in store.records(dates={"2026-10-15"})] == ["v2"]
        assert store.posted_date("v1") == "2026-10-14"

    def test_survives_reopen(self, tmp_path):
        path = str(tmp_path / "jobs.db")
        s1 = JobsStore(path)
        add(s1, "v1", "QA", [src(1, 1)])
        s1.close()
        s2 = JobsStore(path)
        assert s2.count() == 1 and s2.has_post(1, 1)
        s2.close()


class TestVacancyLinks:
    def test_same_vacancy_link_finds_the_vacancy(self, store):
        add(store, "v1", "QA", [src(1, 1)], apply_urls=["https://hh.ru/vacancy/123?from=tg"])
        assert store.find_by_url(["https://spb.hh.ru/vacancy/123"]) is None  # other host
        assert store.find_by_url(["https://hh.ru/vacancy/123"]) == "v1"
        assert store.find_by_url(["https://company.com/careers"]) is None

    def test_a_post_with_many_vacancy_links_is_not_indexed(self, store):
        links = [f"https://hh.ru/vacancy/{n}" for n in (1, 2, 3)]
        add(store, "v1", "QA digest", [src(1, 1)], apply_urls=links)
        assert store.find_by_url(["https://hh.ru/vacancy/2"]) is None

    def test_attach_adds_links_of_the_copy(self, store):
        add(store, "v1", "QA", [src(1, 1)])
        store.attach("v1", {"apply_urls": ["https://ingamejob.com/job/senior-qa-3"]}, [],
                     [src(2, 1)])
        assert store.find_by_url(["https://ingamejob.com/job/senior-qa-3"]) == "v1"


class TestListItems:
    def test_items_of_one_post_are_separate_sources(self, store):
        add(store, "a", "QA one", [_item(src(1, 10), 1)])
        add(store, "b", "QA two", [_item(src(1, 10), 2)])
        assert store.count() == 2 and store.has_post(1, 10)


def _item(source, index):
    return dataclasses.replace(source, item=index)


def test_old_database_is_migrated(tmp_path):
    path = tmp_path / "jobs.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE vacancy_sources (chat_id INTEGER NOT NULL, msg_id INTEGER NOT NULL, "
        "vacancy_id TEXT NOT NULL, kind TEXT NOT NULL, channel TEXT, channel_title TEXT, "
        "message_link TEXT, posted_at TEXT, views INTEGER, seen_at TEXT NOT NULL, "
        "PRIMARY KEY (chat_id, msg_id));"
        "INSERT INTO vacancy_sources VALUES (1, 5, 'v1', 'post', 'qa', 'QA', 'l', NULL, 1, 'x');"
    )
    conn.commit()
    conn.close()
    s = JobsStore(str(path))
    assert s.has_post(1, 5)
    cols = [r[1] for r in s._conn.execute("PRAGMA table_info(vacancy_sources)")]
    assert "item" in cols
    s.close()


class TestRejected:
    def test_rejected_posts_by_day(self, store):
        store.add_rejected(1, 5, "https://t.me/qa/5", "QA", "2026-10-15T10:00:00+03:00",
                           "anti: #резюме", "QA Engineer, 4 года")
        store.add_rejected(1, 5, "https://t.me/qa/5", "QA", "2026-10-15T10:00:00+03:00",
                           "anti: #резюме", "QA Engineer, 4 года")  # seen twice: kept once
        assert store.rejected("2026-10-15") == [{
            "message_link": "https://t.me/qa/5", "channel_title": "QA",
            "posted_at": "2026-10-15T10:00:00+03:00", "reason": "anti: #резюме",
            "title": "QA Engineer, 4 года",
        }]
        assert store.rejected("2026-10-16") == []
