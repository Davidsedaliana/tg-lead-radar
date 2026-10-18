"""The export against docs/vacancy-format.md — the format shared with
hh-vacancy-scraper. The field table is read from the document itself, so a
change made there and not here (or the other way round) fails these tests."""

import json
import re
from pathlib import Path

import pytest

from tg_lead_radar.jobs.config import load_jobs_config
from tg_lead_radar.jobs.dedup import fingerprint, normalize
from tg_lead_radar.jobs.export import CONTRACT_FIELDS, contract_record, export
from tg_lead_radar.jobs.store import JobsStore, Source

DOC = Path(__file__).resolve().parents[1] / "docs" / "vacancy-format.md"
CFG = load_jobs_config().dedup

TYPES = {
    "string": lambda v: isinstance(v, str),
    "string | null": lambda v: v is None or isinstance(v, str),
    "number | null": lambda v: v is None or (
        isinstance(v, int | float) and not isinstance(v, bool)
    ),
    "boolean": lambda v: isinstance(v, bool),
    "boolean | null": lambda v: v is None or isinstance(v, bool),
    "string[]": lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
    "object": lambda v: isinstance(v, dict),
}
ENUMS = {
    "source": {"tg"},
    "format": {"office", "hybrid", "remote", None},
    "grade": {"junior", "middle", "senior", "lead", None},
    "salary_period": {"month", "hour", "day", "shift", "week", "year", "project", None},
}


def doc_fields() -> list[tuple[str, str]]:
    """(name, type) rows of the "Поля" table, in document order."""
    section = DOC.read_text(encoding="utf-8").split("## Поля", 1)[1].split("\n## ", 1)[0]
    rows = []
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
        kind = cells[1].replace("\\|", "|")
        rows += [(name, kind) for name in re.findall(r"`([a-z_]+)`", cells[0])]
    return rows


@pytest.fixture
def store(tmp_path):
    s = JobsStore(str(tmp_path / "jobs.db"))
    yield s
    s.close()


def add(store, vid, text, channel="qa_jobs", chat_id=1, **fields):
    record = {"text": text, "matched_stems": ["qa"], **fields}
    source = Source(
        chat_id=chat_id, msg_id=1, kind="post", channel=channel, channel_title="QA Jobs",
        message_link=f"https://t.me/{channel or f'c/{chat_id}'}/1",
        posted_at="2026-10-15T14:03:00+03:00", views=1520,
    )
    store.add_vacancy(vid, fingerprint(text, fields, CFG), normalize(text), record, [source])


FULL = dict(
    title="Senior QA Automation", company="FinTech Lab", salary_from=250_000,
    salary_to=320_000, currency="RUB", salary_period="month", salary_gross=False,
    format="remote", city="Москва", grade=["middle", "senior"], stack=["pytest"],
    contacts=["@hr_anna", "hr@lab.io"], apply_urls=["https://hh.ru/vacancy/123"],
    same_as=["hh:123"], links=["https://hh.ru/vacancy/123"],
    attachments=[{"type": "document", "file_name": "JD.pdf"}],
    original={"kind": "forward", "message_link": "https://t.me/origin/5"},
)


def test_field_list_matches_the_document():
    assert list(CONTRACT_FIELDS) == [name for name, _ in doc_fields()]


@pytest.mark.parametrize("fields", [FULL, {}], ids=["full", "sparse"])
def test_every_record_follows_the_table(store, tmp_path, fields):
    add(store, "a1b2c3d4e5f60718", "Senior QA Automation\nУдалёнка", **fields)
    [line] = export(store, tmp_path).jsonl.read_text(encoding="utf-8").splitlines()
    rec = json.loads(line)
    assert list(rec) == [name for name, _ in doc_fields()]
    for name, kind in doc_fields():
        assert TYPES[kind](rec[name]), f"{name}: {rec[name]!r} is not {kind}"
    for name, allowed in ENUMS.items():
        assert rec[name] in allowed, name
    assert re.fullmatch(r"tg:[0-9a-f]{16}", rec["id"])
    assert rec["archived"] is False
    assert all(re.fullmatch(r"hh:\d+", x) for x in rec["same_as"])


def test_tg_specifics_go_to_raw(store, tmp_path):
    add(store, "a1b2c3d4e5f60718", "QA", **FULL)
    rec = contract_record(store.records()[0])
    assert {"channel", "channel_title", "views", "sources", "links", "attachments",
            "original", "matched_stems", "grades", "updated_at"} <= set(rec["raw"])
    assert rec["raw"]["grades"] == ["middle", "senior"]
    assert rec["search_name"] == "qa_jobs" and rec["url"] == "https://t.me/qa_jobs/1"


def test_one_grade_the_most_senior(store):
    add(store, "a1", "QA", grade=["middle", "senior"])
    assert contract_record(store.records()[0])["grade"] == "senior"


def test_grade_from_experience_when_the_title_has_none(store):
    add(store, "a1", "QA", experience_grade="middle")
    assert contract_record(store.records()[0])["grade"] == "middle"


def test_private_channel_search_name(store):
    add(store, "a1", "QA", channel=None, chat_id=1234567)
    assert contract_record(store.records()[0])["search_name"] == "c/1234567"
