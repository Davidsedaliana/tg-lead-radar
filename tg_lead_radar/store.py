import sqlite3
from datetime import UTC, datetime
from pathlib import Path

# chat_id is an int in userbot mode and a channel username (str) in web mode —
# sqlite type affinity stores both fine.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    chat_id NOT NULL,
    msg_id INTEGER NOT NULL,
    categories TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, msg_id)
);
"""

ChatKey = int | str


class SeenStore:
    """Deduplication: a matched message is notified about exactly once,
    across restarts (edits re-fire Telethon events)."""

    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def has_chat(self, chat_id: ChatKey) -> bool:
        cur = self._conn.execute("SELECT 1 FROM seen WHERE chat_id = ? LIMIT 1", (chat_id,))
        return cur.fetchone() is not None

    def is_seen(self, chat_id: ChatKey, msg_id: int) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM seen WHERE chat_id = ? AND msg_id = ?", (chat_id, msg_id)
        )
        return cur.fetchone() is not None

    def mark_seen(self, chat_id: ChatKey, msg_id: int, categories: list[str]) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        self._conn.execute(
            "INSERT OR IGNORE INTO seen (chat_id, msg_id, categories, created_at) "
            "VALUES (?, ?, ?, ?)",
            (chat_id, msg_id, ",".join(categories), now),
        )
        self._conn.commit()

    def count(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM seen")
        return cur.fetchone()[0]
