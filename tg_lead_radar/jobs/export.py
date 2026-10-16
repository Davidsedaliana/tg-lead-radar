"""Export: vacancies.jsonl for the external agent, YYYY-MM-DD.md digests for
a human, and the optional Saved Messages card.

Files are replaced atomically, so an agent reading the folder never sees
half a file.
"""

import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from ..card import excerpt, format_published
from .store import JobsStore
from .text import is_hashtag_line

JSONL_NAME = "vacancies.jsonl"
# Revision of the last `export --since`: everything after it is "new".
MARK_KEY = "export_rev"

FORMAT_LABELS = {"remote": "удалёнка", "hybrid": "гибрид", "office": "офис"}
PERIOD_LABELS = {"hour": "в час", "month": "в месяц", "year": "в год"}
ORIGIN_LABELS = {"forward": "переслано из", "link": "репост ссылкой на"}


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    # Windows refuses to replace a file another program has open for a moment.
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                os.unlink(tmp)
                raise
            time.sleep(0.2)


def vacancy_title(text: str) -> str:
    """The first line that is not just hashtags — the vacancy's name, usually."""
    for line in text.split("\n"):
        line = line.strip()
        if line and not is_hashtag_line(line):
            return line[:120]
    return "Без названия"


def _num(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def format_salary(rec: dict) -> str | None:
    lo, hi = rec.get("salary_from"), rec.get("salary_to")
    if lo is None and hi is None:
        return None
    if lo is not None and hi is not None:
        text = _num(lo) if lo == hi else f"{_num(lo)} – {_num(hi)}"
    elif lo is not None:
        text = f"от {_num(lo)}"
    else:
        text = f"до {_num(hi)}"
    if rec.get("currency"):
        text += f" {rec['currency']}"
    if rec.get("salary_period"):
        text += f" {PERIOD_LABELS.get(rec['salary_period'], rec['salary_period'])}"
    return text


def _fence(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def _link(title: str | None, url: str | None) -> str:
    title = (title or "канал").replace("[", "(").replace("]", ")")
    return f"[{title}]({url})" if url else title


def _field_lines(rec: dict) -> list[str]:
    lines = []
    salary = format_salary(rec)
    if salary:
        lines.append(f"- **Зарплата:** {salary}")
    meta = []
    if rec.get("format"):
        meta.append(f"**Формат:** {FORMAT_LABELS.get(rec['format'], rec['format'])}")
    if rec.get("city"):
        meta.append(f"**Город:** {rec['city']}")
    if rec.get("grade"):
        meta.append(f"**Грейд:** {', '.join(rec['grade'])}")
    if meta:
        lines.append("- " + " · ".join(meta))
    for label, key in (("Стек", "stack"), ("Контакты", "contacts"), ("Откликнуться", "apply_urls")):
        if rec.get(key):
            lines.append(f"- **{label}:** {', '.join(rec[key])}")
    if rec.get("attachments"):
        files = [f"{a.get('file_name') or 'без имени'} ({a['type']})" for a in rec["attachments"]]
        lines.append(f"- **Вложения:** {', '.join(files)}")
    return lines


def render_vacancy(index: int, rec: dict) -> str:
    head = [
        f"## {index}. {vacancy_title(rec['text'])}",
        "",
    ]
    where = [_link(rec.get("channel_title"), _channel_url(rec))]
    if rec.get("posted_at"):
        where.append(format_published(rec["posted_at"]))
    if rec.get("views") is not None:
        where.append(f"👁 {_num(rec['views'])}")
    lines = [f"- **Канал:** {' · '.join(where)}"]
    if rec.get("message_link"):
        lines.append(f"- **Пост:** {rec['message_link']}")
    others = [
        _link(s.get("channel_title"), s.get("message_link"))
        for s in rec.get("sources", [])
        if s.get("kind") == "post" and s.get("message_link") != rec.get("message_link")
    ]
    if others:
        lines.append(f"- **Также в:** {' · '.join(others)}")
    original = rec.get("original")
    if original and original.get("message_link"):
        label = ORIGIN_LABELS.get(original.get("kind"), "оригинал")
        source = original.get("channel_title") or original.get("channel") or "?"
        lines.append(f"- **Оригинал:** {original['message_link']} ({label} {source})")
    lines += _field_lines(rec)
    body = [*head, *lines, "", _block(rec["text"])]
    if original and original.get("text"):
        body += ["", "**Текст оригинала:**", "", _block(original["text"])]
    return "\n".join(body)


def _block(text: str) -> str:
    fence = _fence(text)
    return f"{fence}text\n{text}\n{fence}"


def _channel_url(rec: dict) -> str | None:
    channel = rec.get("channel")
    if channel and not channel.isdigit():
        return f"https://t.me/{channel}"
    return rec.get("message_link")


def render_digest(day: str, records: list[dict]) -> str:
    yyyy, mm, dd = day.split("-")
    channels = sorted({r.get("channel_title") or r.get("channel") or "?" for r in records})
    parts = [
        f"# Вакансии QA — {dd}.{mm}.{yyyy}",
        "",
        f"Вакансий: {len(records)} · каналы: {', '.join(channels)}",
    ]
    for i, rec in enumerate(records, 1):
        parts += ["", "---", "", render_vacancy(i, rec)]
    return "\n".join(parts) + "\n"


def render_card(rec: dict) -> str:
    """Saved Messages card for a fresh vacancy (JOBS_NOTIFY=true)."""
    lines = [f"💼 {vacancy_title(rec['text'])}"]
    where = [rec.get("channel_title") or rec.get("channel") or ""]
    if rec.get("posted_at"):
        where.append(format_published(rec["posted_at"]))
    lines.append(" · ".join(w for w in where if w))
    meta = [FORMAT_LABELS.get(rec.get("format") or ""), ", ".join(rec.get("grade") or [])]
    meta = [m for m in meta if m]
    salary = format_salary(rec)
    if salary:
        meta.insert(0, f"💰 {salary}")
    if meta:
        lines.append(" · ".join(meta))
    if rec.get("message_link"):
        lines.append(rec["message_link"])
    lines += ["", excerpt(rec["text"])]
    return "\n".join(lines)


@dataclass(frozen=True)
class ExportResult:
    vacancies: int
    jsonl: Path
    digests: list[Path]


def write_digests(store: JobsStore, out_dir: str | Path, days: set[str]) -> list[Path]:
    """Rebuild the digests of these days in full — a day gets new vacancies
    over hours, and a digest must always list the whole day."""
    out = Path(out_dir)
    written = []
    for day in sorted(days):
        path = out / f"{day}.md"
        atomic_write(path, render_digest(day, store.records(dates={day})))
        written.append(path)
    return written


def export(store: JobsStore, out_dir: str | Path, since: bool = False) -> ExportResult:
    """Full export, or with since=True only what is new or updated since the
    previous `since` export. Only `since` moves the mark, so a full export
    run by hand does not swallow what the agent has not read yet."""
    out = Path(out_dir)
    upto = store.rev()
    after = int(store.get_meta(MARK_KEY) or 0) if since else None
    records = store.records(after_rev=after, upto_rev=upto)
    jsonl = out / JSONL_NAME
    atomic_write(jsonl, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
    days = {r["posted_at"][:10] for r in records if r.get("posted_at")}
    digests = write_digests(store, out, days)
    if since:
        store.set_meta(MARK_KEY, str(upto))
    return ExportResult(vacancies=len(records), jsonl=jsonl, digests=digests)
