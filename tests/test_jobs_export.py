import json

import pytest

import tg_lead_radar.jobs.__main__ as cli
from tg_lead_radar.config import Settings
from tg_lead_radar.jobs.config import load_jobs_config
from tg_lead_radar.jobs.dedup import fingerprint, normalize
from tg_lead_radar.jobs.export import (
    export,
    format_salary,
    render_card,
    render_digest,
    vacancy_title,
)
from tg_lead_radar.jobs.store import JobsStore, Source

CFG = load_jobs_config().dedup

TEXT = "#вакансия #qa\nQA Automation Engineer (Senior)\nУдалёнка\n\nКод: `pytest -k smoke`"


@pytest.fixture
def store(tmp_path):
    s = JobsStore(str(tmp_path / "jobs.db"))
    yield s
    s.close()


def src(chat_id, msg_id, day=15, channel="qa_jobs", kind="post"):
    return Source(
        chat_id=chat_id, msg_id=msg_id, kind=kind, channel=channel, channel_title="QA Jobs",
        message_link=f"https://t.me/{channel}/{msg_id}",
        posted_at=f"2026-10-{day:02d}T14:03:00+03:00", views=1520,
    )


def add(store, vid, text=TEXT, day=15, msg_id=1, **fields):
    record = {
        "text": text, "matched_stems": ["qa"], "links": [], "attachments": [],
        "original": None, **fields,
    }
    store.add_vacancy(
        vid, fingerprint(text, fields, CFG), normalize(text), record, [src(1, msg_id, day)]
    )


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class TestExport:
    def test_full_export_writes_jsonl_and_digests(self, store, tmp_path):
        add(store, "v1", day=14, msg_id=1, salary_from=300_000, currency="RUB")
        add(store, "v2", text="QA Engineer", day=15, msg_id=2)
        result = export(store, tmp_path / "out")
        assert result.jsonl.name == "tg_vacancies.jsonl"
        rows = read_jsonl(result.jsonl)
        assert [r["id"] for r in rows] == ["tg:v1", "tg:v2"]
        assert rows[0]["text"] == TEXT  # full text, line breaks kept
        assert rows[0]["salary_from"] == 300_000
        assert rows[0]["posted_at"] == "2026-10-14T14:03:00+03:00"
        assert rows[0]["url"] == "https://t.me/qa_jobs/1"
        assert rows[0]["raw"]["sources"][0]["message_link"] == "https://t.me/qa_jobs/1"
        assert sorted(p.name for p in result.digests) == [
            "tg_2026-10-14.md", "tg_2026-10-15.md",
        ]

    def test_jsonl_is_utf8_not_escaped(self, store, tmp_path):
        add(store, "v1")
        result = export(store, tmp_path)
        assert "Удалёнка" in result.jsonl.read_text(encoding="utf-8")

    def test_since_exports_only_what_changed(self, store, tmp_path):
        add(store, "v1", msg_id=1)
        assert export(store, tmp_path, since=True).vacancies == 1
        assert export(store, tmp_path, since=True).vacancies == 0
        assert read_jsonl(tmp_path / "tg_vacancies.jsonl") == []

        add(store, "v2", text="QA Engineer", msg_id=2)
        store.attach("v1", {}, [], [src(2, 9, channel="it_remote")])
        rows = read_jsonl(export(store, tmp_path, since=True).jsonl)
        assert [r["id"] for r in rows] == ["tg:v1", "tg:v2"]
        assert len(rows[0]["raw"]["sources"]) == 2

    def test_full_export_does_not_move_the_since_mark(self, store, tmp_path):
        add(store, "v1")
        export(store, tmp_path)
        assert export(store, tmp_path, since=True).vacancies == 1

    def test_digest_of_a_day_lists_the_whole_day(self, store, tmp_path):
        add(store, "v1", msg_id=1)
        export(store, tmp_path, since=True)
        add(store, "v2", text="QA Engineer", msg_id=2)
        export(store, tmp_path, since=True)
        digest = (tmp_path / "tg_2026-10-15.md").read_text(encoding="utf-8")
        assert "Вакансий: 2" in digest


class TestRender:
    def test_digest(self, store):
        add(
            store, "v1", salary_from=250_000, salary_to=320_000, currency="RUB",
            format="remote", grade=["senior"], stack=["pytest"], contacts=["@hr_anna"],
            apply_urls=["https://hh.ru/vacancy/1"],
            attachments=[{"type": "document", "file_name": "JD.pdf"}],
        )
        store.attach("v1", {}, [], [src(2, 9, day=16, channel="it_remote")])
        digest = render_digest("2026-10-15", store.records())
        assert digest.startswith("# Вакансии QA — 15.10.2026")
        assert "## 1. QA Automation Engineer (Senior)" in digest
        assert "[QA Jobs](https://t.me/qa_jobs) · 15.10 14:03 мск · 👁 1 520" in digest
        assert "- **Пост:** https://t.me/qa_jobs/1" in digest
        assert "- **Также в:** [QA Jobs](https://t.me/it_remote/9)" in digest
        assert "- **Зарплата:** 250 000 – 320 000 RUB" in digest
        assert "**Формат:** удалёнка · **Грейд:** senior" in digest
        assert "- **Откликнуться:** https://hh.ru/vacancy/1" in digest
        assert "- **Вложения:** JD.pdf (document)" in digest
        # the full text in a fence longer than any backtick run inside it
        assert "```text\n" + TEXT + "\n```" in digest

    def test_digest_shows_the_original(self, store):
        original = {
            "kind": "link", "channel": "origin", "channel_title": "Origin",
            "message_link": "https://t.me/origin/5", "text": "Полный текст вакансии",
        }
        add(store, "v1", text="QA: t.me/origin/5", original=original)
        digest = render_digest("2026-10-15", store.records())
        assert "- **Оригинал:** https://t.me/origin/5 (репост ссылкой на Origin)" in digest
        assert "**Текст оригинала:**\n\n```text\nПолный текст вакансии\n```" in digest

    def test_card(self, store):
        add(store, "v1", salary_from=300_000, currency="RUB", format="remote", grade=["senior"])
        card = render_card(store.records()[0])
        assert card.split("\n")[:4] == [
            "💼 QA Automation Engineer (Senior)",
            "QA Jobs · 15.10 14:03 мск",
            "💰 от 300 000 RUB · удалёнка · senior",
            "https://t.me/qa_jobs/1",
        ]

    @pytest.mark.parametrize(
        ("fields", "expected"),
        [
            ({"salary_from": 200_000, "salary_to": 200_000, "currency": "RUB"}, "200 000 RUB"),
            ({"salary_to": 4000, "currency": "USD", "salary_period": "month"},
             "до 4 000 USD в месяц"),
            ({"salary_from": 2000, "salary_period": "hour"}, "от 2 000 в час"),
            ({}, None),
        ],
    )
    def test_format_salary(self, fields, expected):
        assert format_salary(fields) == expected

    def test_title_skips_hashtags(self):
        assert vacancy_title("#qa #remote\n\nQA Engineer") == "QA Engineer"


def test_cli_export(tmp_path, monkeypatch, capsys):
    db = tmp_path / "jobs.db"
    s = JobsStore(str(db))
    add(s, "v1")
    s.close()
    settings = Settings(jobs_db_path=str(db), jobs_export_dir=str(tmp_path / "out"), _env_file=None)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    assert cli.main(["export", "--since-last"]) == 0
    assert "Вакансий новых и обновлённых: 1" in capsys.readouterr().out
    assert (tmp_path / "out" / "tg_vacancies.jsonl").exists()
    assert cli.main(["export", "--since"]) == 0  # the old name still works
    assert "новых и обновлённых: 0" in capsys.readouterr().out
