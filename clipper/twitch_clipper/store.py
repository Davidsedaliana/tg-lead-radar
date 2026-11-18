from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS streamers (
    name    TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS clips (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    streamer   TEXT NOT NULL,
    created_at REAL NOT NULL,
    chat_mult  REAL,
    audio_mult REAL,
    score      INTEGER,
    category   TEXT,
    summary    TEXT
);
CREATE TABLE IF NOT EXISTS votes (
    clip_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    value   INTEGER NOT NULL,
    PRIMARY KEY (clip_id, user_id)
);
"""


# Columns added after the first version; created on start if the database is older.
CLIP_COLUMNS = {
    "start_ts": "REAL",  # the clip's current boundaries, wall-clock time
    "end_ts": "REAL",
    "caption": "TEXT",
    "source_path": "TEXT",  # wider recording around the clip, for the trim buttons
    "source_start": "REAL",
    "source_end": "REAL",
}


@dataclass(frozen=True)
class ClipRow:
    id: int
    streamer: str
    created_at: float
    start: float | None
    end: float | None
    caption: str
    source_path: str
    source_start: float | None
    source_end: float | None


class Store:
    def __init__(self, path: Path | str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)
        existing = {row[1] for row in self.db.execute("PRAGMA table_info(clips)")}
        for column, kind in CLIP_COLUMNS.items():
            if column not in existing:
                self.db.execute(f"ALTER TABLE clips ADD COLUMN {column} {kind}")
        self.db.commit()

    # ---- streamers -------------------------------------------------------

    def seed(self, names: tuple[str, ...]) -> None:
        """Add the initial list from .env only when the table is still empty."""
        if self.db.execute("SELECT 1 FROM streamers LIMIT 1").fetchone() is None:
            for n in names:
                self.add_streamer(n)

    def add_streamer(self, name: str) -> bool:
        cur = self.db.execute("INSERT OR IGNORE INTO streamers (name) VALUES (?)", (name,))
        self.db.commit()
        return cur.rowcount > 0

    def remove_streamer(self, name: str) -> bool:
        cur = self.db.execute("DELETE FROM streamers WHERE name = ?", (name,))
        self.db.commit()
        return cur.rowcount > 0

    def toggle_streamer(self, name: str) -> bool | None:
        """Flip the 'cut clips' switch. Returns the new state, None if unknown."""
        row = self.db.execute("SELECT enabled FROM streamers WHERE name = ?", (name,)).fetchone()
        if row is None:
            return None
        new = 0 if row[0] else 1
        self.db.execute("UPDATE streamers SET enabled = ? WHERE name = ?", (new, name))
        self.db.commit()
        return bool(new)

    def streamers(self) -> list[tuple[str, bool]]:
        rows = self.db.execute("SELECT name, enabled FROM streamers ORDER BY name")
        return [(name, bool(enabled)) for name, enabled in rows]

    def enabled_streamers(self) -> set[str]:
        return {name for name, enabled in self.streamers() if enabled}

    # ---- clips and votes -------------------------------------------------

    def add_clip(
        self,
        streamer: str,
        chat_mult: float,
        audio_mult: float | None,
        score: int | None,
        category: str,
        summary: str,
    ) -> int:
        cur = self.db.execute(
            "INSERT INTO clips (streamer, created_at, chat_mult, audio_mult, score, category,"
            " summary) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (streamer, time.time(), chat_mult, audio_mult, score, category, summary),
        )
        self.db.commit()
        return int(cur.lastrowid or 0)

    def set_clip_range(
        self, clip_id: int, start: float, end: float, caption: str | None = None
    ) -> None:
        self.db.execute(
            "UPDATE clips SET start_ts = ?, end_ts = ?, caption = COALESCE(?, caption)"
            " WHERE id = ?",
            (start, end, caption, clip_id),
        )
        self.db.commit()

    def set_source(self, clip_id: int, path: Path | str, start: float, end: float) -> None:
        self.db.execute(
            "UPDATE clips SET source_path = ?, source_start = ?, source_end = ? WHERE id = ?",
            (str(path), start, end, clip_id),
        )
        self.db.commit()

    def get_clip(self, clip_id: int) -> ClipRow | None:
        row = self.db.execute(
            "SELECT id, streamer, created_at, start_ts, end_ts, caption, source_path,"
            " source_start, source_end FROM clips WHERE id = ?",
            (clip_id,),
        ).fetchone()
        if row is None:
            return None
        return ClipRow(row[0], row[1], row[2], row[3], row[4], row[5] or "", row[6] or "",
                       row[7], row[8])  # fmt: skip

    def pop_old_sources(self, created_before: float) -> list[str]:
        """Forget the saved sources of old clips and return their paths for deletion."""
        rows = self.db.execute(
            "SELECT source_path FROM clips WHERE source_path IS NOT NULL AND created_at < ?",
            (created_before,),
        ).fetchall()
        self.db.execute(
            "UPDATE clips SET source_path = NULL WHERE source_path IS NOT NULL AND created_at < ?",
            (created_before,),
        )
        self.db.commit()
        return [row[0] for row in rows]

    def source_paths(self) -> set[str]:
        rows = self.db.execute("SELECT source_path FROM clips WHERE source_path IS NOT NULL")
        return {row[0] for row in rows}

    def forget_source(self, path: Path | str) -> None:
        self.db.execute("UPDATE clips SET source_path = NULL WHERE source_path = ?", (str(path),))
        self.db.commit()

    def vote(self, clip_id: int, user_id: int, value: int) -> tuple[int, int]:
        """Record a vote (a repeated identical vote removes it). Returns (likes, dislikes)."""
        row = self.db.execute(
            "SELECT value FROM votes WHERE clip_id = ? AND user_id = ?", (clip_id, user_id)
        ).fetchone()
        if row is not None and row[0] == value:
            self.db.execute(
                "DELETE FROM votes WHERE clip_id = ? AND user_id = ?", (clip_id, user_id)
            )
        else:
            self.db.execute(
                "INSERT OR REPLACE INTO votes (clip_id, user_id, value) VALUES (?, ?, ?)",
                (clip_id, user_id, value),
            )
        self.db.commit()
        return self.votes(clip_id)

    def votes(self, clip_id: int) -> tuple[int, int]:
        likes, dislikes = self.db.execute(
            "SELECT COALESCE(SUM(value = 1), 0), COALESCE(SUM(value = -1), 0)"
            " FROM votes WHERE clip_id = ?",
            (clip_id,),
        ).fetchone()
        return int(likes), int(dislikes)
