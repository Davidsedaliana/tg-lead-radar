"""SQLite storage of the jobs mode (data/jobs.db, apart from the orders' seen.db).

A vacancy is stored once with every post it was seen in. The export command
reads this file while the radar keeps writing to it, hence WAL mode.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from .dedup import Fingerprint, shingles
from .lists import vacancy_url_key
from .text import unique

MSK = timezone(timedelta(hours=3))
MAX_VACANCY_LINKS = 2

# Fields a later copy of a vacancy may fill in when the first one lacked them.
FIELD_KEYS = (
    "company", "salary_from", "salary_to", "currency", "salary_period", "salary_gross",
    "format", "city", "grade", "experience_grade", "stack", "contacts", "apply_urls",
    "same_as", "attachments",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs_channels (
    chat_id INTEGER PRIMARY KEY,
    username TEXT,
    title TEXT,
    last_msg_id INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vacancies (
    id TEXT PRIMARY KEY,
    text_hash TEXT NOT NULL,
    title_key TEXT NOT NULL,
    norm_text TEXT NOT NULL,
    record TEXT NOT NULL,
    posted_at TEXT,
    first_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    rev INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS vacancies_hash ON vacancies (text_hash);
CREATE INDEX IF NOT EXISTS vacancies_posted ON vacancies (posted_at);
CREATE INDEX IF NOT EXISTS vacancies_rev ON vacancies (rev);
CREATE TABLE IF NOT EXISTS vacancy_sources (
    chat_id INTEGER NOT NULL,
    msg_id INTEGER NOT NULL,
    item INTEGER NOT NULL DEFAULT 0,
    vacancy_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    channel TEXT,
    channel_title TEXT,
    message_link TEXT,
    posted_at TEXT,
    views INTEGER,
    seen_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, msg_id, item)
);
CREATE INDEX IF NOT EXISTS vacancy_sources_vacancy ON vacancy_sources (vacancy_id);
CREATE TABLE IF NOT EXISTS vacancy_urls (
    url_key TEXT PRIMARY KEY,
    vacancy_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs_rejected (
    chat_id INTEGER NOT NULL,
    msg_id INTEGER NOT NULL,
    message_link TEXT,
    channel_title TEXT,
    posted_at TEXT,
    reason TEXT NOT NULL,
    title TEXT,
    seen_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, msg_id)
);
CREATE INDEX IF NOT EXISTS jobs_rejected_posted ON jobs_rejected (posted_at);
"""


def to_iso(dt: datetime | None) -> str | None:
    """Moscow time with its offset. One offset for every stored timestamp
    keeps them comparable as strings and makes the date prefix the MSK date."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(MSK).isoformat(timespec="seconds")


def now_iso() -> str:
    return to_iso(datetime.now(UTC))


@dataclass(frozen=True)
class Source:
    """A post the vacancy was seen in. kind: "post" — read in a watched
    channel; "forward" / "link" — the original that post points to."""

    chat_id: int
    msg_id: int
    kind: str
    channel: str | None
    channel_title: str | None
    message_link: str | None
    posted_at: str | None
    views: int | None = None
    item: int = 0  # position in a list post ("Подборка вакансий"), 0 for a whole post


class JobsStore:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._shingles: dict[str, frozenset[str]] = {}

    def close(self) -> None:
        self._conn.close()

    def _migrate(self) -> None:
        """A database from before list posts has no `item` in vacancy_sources;
        SQLite cannot change a primary key in place, so the table is rebuilt."""
        columns = [r[1] for r in self._conn.execute("PRAGMA table_info(vacancy_sources)")]
        if columns and "item" not in columns:
            self._conn.execute("ALTER TABLE vacancy_sources RENAME TO vacancy_sources_old")
            self._conn.executescript(_SCHEMA)
            self._conn.execute(
                "INSERT INTO vacancy_sources (chat_id, msg_id, item, vacancy_id, kind, channel, "
                "channel_title, message_link, posted_at, views, seen_at) "
                "SELECT chat_id, msg_id, 0, vacancy_id, kind, channel, channel_title, "
                "message_link, posted_at, views, seen_at FROM vacancy_sources_old"
            )
            self._conn.execute("DROP TABLE vacancy_sources_old")
            self._conn.commit()

    # --- channels ---

    def last_msg_id(self, chat_id: int) -> int | None:
        row = self._conn.execute(
            "SELECT last_msg_id FROM jobs_channels WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return row[0] if row else None

    def set_last_msg_id(self, chat_id: int, username: str | None, title: str, msg_id: int) -> None:
        self._conn.execute(
            "INSERT INTO jobs_channels (chat_id, username, title, last_msg_id, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (chat_id) DO UPDATE SET "
            "username = excluded.username, title = excluded.title, "
            "last_msg_id = MAX(last_msg_id, excluded.last_msg_id), "
            "updated_at = excluded.updated_at",
            (chat_id, username, title, msg_id, now_iso()),
        )
        self._conn.commit()

    # --- meta ---

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    def rev(self) -> int:
        """Change counter: every new or updated vacancy gets the next value,
        so "what changed since the last export" needs no clock."""
        return int(self.get_meta("rev") or 0)

    def _next_rev(self) -> int:
        rev = self.rev() + 1
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES ('rev', ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (str(rev),),
        )
        return rev

    # --- vacancies ---

    def has_post(self, chat_id: int, msg_id: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM vacancy_sources WHERE chat_id = ? AND msg_id = ? AND kind = 'post'",
            (chat_id, msg_id),
        ).fetchone()
        return row is not None

    @staticmethod
    def _url_keys(urls: list[str] | None) -> list[str]:
        """Vacancy links of a record. A post with more than two is a list that
        slipped past detection — its links name other vacancies too."""
        keys = unique(filter(None, map(vacancy_url_key, urls or [])))
        return keys if len(keys) <= MAX_VACANCY_LINKS else []

    def find_by_url(self, urls: list[str] | None) -> str | None:
        """A vacancy already known under one of these vacancy links."""
        for key in self._url_keys(urls):
            row = self._conn.execute(
                "SELECT vacancy_id FROM vacancy_urls WHERE url_key = ?", (key,)
            ).fetchone()
            if row:
                return row[0]
        return None

    def _add_urls(self, vacancy_id: str, urls: list[str] | None) -> None:
        for key in self._url_keys(urls):
            self._conn.execute(
                "INSERT OR IGNORE INTO vacancy_urls (url_key, vacancy_id) VALUES (?, ?)",
                (key, vacancy_id),
            )

    def find_by_hash(self, text_hash: str) -> str | None:
        row = self._conn.execute(
            "SELECT id FROM vacancies WHERE text_hash = ? LIMIT 1", (text_hash,)
        ).fetchone()
        return row[0] if row else None

    def candidates(self, posted_since: str) -> list[tuple[str, Fingerprint]]:
        """Vacancies recent enough to be compared with a new post."""
        rows = self._conn.execute(
            "SELECT id, text_hash, title_key, norm_text, record FROM vacancies "
            "WHERE posted_at IS NULL OR posted_at >= ?",
            (posted_since,),
        ).fetchall()
        out = []
        for row in rows:
            if row["id"] not in self._shingles:
                self._shingles[row["id"]] = shingles(row["norm_text"])
            record = json.loads(row["record"])
            salary = (record.get("salary_from"), record.get("salary_to"), record.get("currency"))
            out.append((row["id"], Fingerprint(
                text_hash=row["text_hash"],
                title_key=row["title_key"],
                shingles=self._shingles[row["id"]],
                grade=tuple(record["grade"]) if record.get("grade") else None,
                salary=salary if salary[0] is not None or salary[1] is not None else None,
            )))
        return out

    def add_vacancy(
        self,
        vacancy_id: str,
        fp: Fingerprint,
        norm_text: str,
        record: dict,
        sources: list[Source],
    ) -> None:
        now = now_iso()
        self._conn.execute(
            "INSERT INTO vacancies (id, text_hash, title_key, norm_text, record, posted_at, "
            "first_seen_at, updated_at, rev) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)",
            (vacancy_id, fp.text_hash, fp.title_key, norm_text,
             json.dumps(record, ensure_ascii=False), now, now, self._next_rev()),
        )
        self._add_sources(vacancy_id, sources, now)
        self._add_urls(vacancy_id, record.get("apply_urls"))
        self._conn.commit()

    def attach(
        self, vacancy_id: str, fields: dict, stems: list[str], sources: list[Source]
    ) -> None:
        """Another copy of a known vacancy: record where it was seen and let
        it fill fields the first copy lacked."""
        now = now_iso()
        row = self._conn.execute(
            "SELECT record FROM vacancies WHERE id = ?", (vacancy_id,)
        ).fetchone()
        record = json.loads(row[0])
        for key in FIELD_KEYS:
            if not record.get(key) and fields.get(key):
                record[key] = fields[key]
        record["matched_stems"] = unique(record.get("matched_stems", []) + list(stems))
        self._conn.execute(
            "UPDATE vacancies SET record = ?, updated_at = ?, rev = ? WHERE id = ?",
            (json.dumps(record, ensure_ascii=False), now, self._next_rev(), vacancy_id),
        )
        self._add_sources(vacancy_id, sources, now)
        self._add_urls(vacancy_id, fields.get("apply_urls"))
        self._conn.commit()

    def _add_sources(self, vacancy_id: str, sources: list[Source], now: str) -> None:
        for s in sources:
            # An original first known from a forward, then read in its own
            # channel, becomes a "post" source with its views.
            self._conn.execute(
                "INSERT INTO vacancy_sources (chat_id, msg_id, item, vacancy_id, kind, channel, "
                "channel_title, message_link, posted_at, views, seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (chat_id, msg_id, item) DO UPDATE SET "
                "kind = CASE WHEN excluded.kind = 'post' THEN 'post' ELSE kind END, "
                "channel = COALESCE(excluded.channel, channel), "
                "channel_title = COALESCE(excluded.channel_title, channel_title), "
                "message_link = COALESCE(excluded.message_link, message_link), "
                "posted_at = COALESCE(posted_at, excluded.posted_at), "
                "views = COALESCE(excluded.views, views)",
                (s.chat_id, s.msg_id, s.item, vacancy_id, s.kind, s.channel, s.channel_title,
                 s.message_link, s.posted_at, s.views, now),
            )
        self._conn.execute(
            "UPDATE vacancies SET posted_at = (SELECT MIN(posted_at) FROM vacancy_sources "
            "WHERE vacancy_id = ? AND kind = 'post') WHERE id = ?",
            (vacancy_id, vacancy_id),
        )

    def posted_date(self, vacancy_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT posted_at FROM vacancies WHERE id = ?", (vacancy_id,)
        ).fetchone()
        return row[0][:10] if row and row[0] else None

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM vacancies").fetchone()[0]

    # --- rejected posts, for tuning the filter ---

    def add_rejected(
        self, chat_id: int, msg_id: int, message_link: str, channel_title: str,
        posted_at: str | None, reason: str, title: str,
    ) -> None:
        """A post with QA words that the filter turned down, and why."""
        self._conn.execute(
            "INSERT OR IGNORE INTO jobs_rejected (chat_id, msg_id, message_link, channel_title, "
            "posted_at, reason, title, seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (chat_id, msg_id, message_link, channel_title, posted_at, reason, title, now_iso()),
        )
        self._conn.commit()

    def rejected(self, day: str) -> list[dict]:
        """Posts of this day (YYYY-MM-DD, Moscow time) the filter turned down."""
        rows = self._conn.execute(
            "SELECT message_link, channel_title, posted_at, reason, title FROM jobs_rejected "
            "WHERE substr(posted_at, 1, 10) = ? ORDER BY posted_at",
            (day,),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- export ---

    def records(
        self,
        after_rev: int | None = None,
        upto_rev: int | None = None,
        dates: set[str] | None = None,
    ) -> list[dict]:
        """Vacancies as export records, oldest post first.

        after_rev/upto_rev select what changed between two exports; dates
        (YYYY-MM-DD, Moscow time) select whole days for the digests.
        """
        query = "SELECT * FROM vacancies WHERE 1 = 1"
        params: list = []
        if after_rev is not None:
            query += " AND rev > ?"
            params.append(after_rev)
        if upto_rev is not None:
            query += " AND rev <= ?"
            params.append(upto_rev)
        if dates is not None:
            query += f" AND substr(posted_at, 1, 10) IN ({','.join('?' * len(dates))})"
            params += sorted(dates)
        query += " ORDER BY posted_at, id"
        return [self._assemble(row) for row in self._conn.execute(query, params).fetchall()]

    def record(self, vacancy_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM vacancies WHERE id = ?", (vacancy_id,)).fetchone()
        return self._assemble(row) if row else None

    def _assemble(self, row: sqlite3.Row) -> dict:
        sources = [
            dict(s) for s in self._conn.execute(
                "SELECT chat_id, kind, channel, channel_title, message_link, posted_at, views "
                "FROM vacancy_sources WHERE vacancy_id = ? "
                "ORDER BY posted_at, chat_id, msg_id, item",
                (row["id"],),
            ).fetchall()
        ]
        for s in sources:
            chat_id = s.pop("chat_id")
            # The shared export format names a private channel "c/<id>".
            s["channel"] = s["channel"] or f"c/{chat_id}"
        posts = [s for s in sources if s["kind"] == "post"] or sources
        primary = posts[0] if posts else {}
        record = json.loads(row["record"])
        return {
            "id": row["id"],
            "posted_at": primary.get("posted_at"),
            "channel": primary.get("channel"),
            "channel_title": primary.get("channel_title"),
            "message_link": primary.get("message_link"),
            "views": primary.get("views"),
            "text": record["text"],
            "title": record.get("title"),
            **{key: record.get(key) for key in FIELD_KEYS if key != "attachments"},
            "links": record.get("links") or [],
            "attachments": record.get("attachments") or [],
            "original": record.get("original"),
            "list_item": record.get("list_item"),
            "sources": sources,
            "matched_stems": record.get("matched_stems", []),
            "first_seen_at": row["first_seen_at"],
            "updated_at": row["updated_at"],
        }
