from __future__ import annotations

import sqlite3
import time
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


class Store:
    def __init__(self, path: Path | str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)
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
