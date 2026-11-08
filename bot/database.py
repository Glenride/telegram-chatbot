"""SQLite persistence: message log, warns, schedules, digest state.

All writes are guarded by a lock so the async handlers and the APScheduler
job thread never corrupt the database.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id   INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    username  TEXT,
    text      TEXT,
    ts        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_chat_ts ON messages(chat_id, ts);

CREATE TABLE IF NOT EXISTS new_members (
    chat_id   INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    username  TEXT,
    joined_ts TEXT NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS warnings (
    chat_id   INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    count     INTEGER NOT NULL DEFAULT 0,
    last_ts   TEXT,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS mod_actions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id   INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    action    TEXT NOT NULL,   -- warn | mute | unmute | ban | unban | delete
    reason    TEXT,
    ts        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mod_chat_ts ON mod_actions(chat_id, ts);

CREATE TABLE IF NOT EXISTS keyword_hits (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id   INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    keyword   TEXT NOT NULL,
    text      TEXT,
    ts        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kw_chat_ts ON keyword_hits(chat_id, ts);

CREATE TABLE IF NOT EXISTS runtime_schedules (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL,
    chat_id   INTEGER NOT NULL,
    cron      TEXT NOT NULL,
    text      TEXT NOT NULL,
    enabled   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS meta (
    key       TEXT PRIMARY KEY,
    value     TEXT
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Thin, locked wrapper around the bot's SQLite database."""

    def __init__(self, path: str) -> None:
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        log.info("Database ready at %s", path)

    # ------------------------------------------------------------- messages
    def log_message(self, chat_id: int, user_id: int, username: str | None,
                    text: str | None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages (chat_id, user_id, username, text, ts)"
                " VALUES (?, ?, ?, ?, ?)",
                (chat_id, user_id, username, (text or "")[:4000], _now_iso()),
            )
            self._conn.commit()

    def recent_messages(self, chat_id: int, since_iso: str,
                        limit: int = 30) -> list[sqlite3.Row]:
        """Recent non-empty messages in one chat, newest first."""
        with self._lock:
            return self._conn.execute(
                "SELECT user_id, username, text, ts FROM messages"
                " WHERE chat_id = ? AND ts >= ? AND text != ''"
                " ORDER BY id DESC LIMIT ?",
                (chat_id, since_iso, limit),
            ).fetchall()

    # ---------------------------------------------------------- new members
    def log_new_member(self, chat_id: int, user_id: int,
                       username: str | None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO new_members (chat_id, user_id, username, joined_ts)"
                " VALUES (?, ?, ?, ?)",
                (chat_id, user_id, username, _now_iso()),
            )
            self._conn.commit()

    def member_joined_within(self, chat_id: int, user_id: int, hours: int) -> bool:
        """True if the user joined this chat less than ``hours`` ago."""
        with self._lock:
            row = self._conn.execute(
                "SELECT joined_ts FROM new_members WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            ).fetchone()
        if not row:
            return False
        joined = datetime.fromisoformat(row["joined_ts"])
        return datetime.now(timezone.utc) - joined < timedelta(hours=hours)

    # -------------------------------------------------------------- warnings
    def add_warning(self, chat_id: int, user_id: int) -> int:
        """Increment the warn count; return the new count."""
        with self._lock:
            row = self._conn.execute(
                "SELECT count FROM warnings WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            ).fetchone()
            count = (row["count"] if row else 0) + 1
            self._conn.execute(
                "INSERT OR REPLACE INTO warnings (chat_id, user_id, count, last_ts)"
                " VALUES (?, ?, ?, ?)",
                (chat_id, user_id, count, _now_iso()),
            )
            self._conn.commit()
        return count

    def reset_warnings(self, chat_id: int, user_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM warnings WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )
            self._conn.commit()

    # ----------------------------------------------------------- mod actions
    def log_mod_action(self, chat_id: int, user_id: int, action: str,
                       reason: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO mod_actions (chat_id, user_id, action, reason, ts)"
                " VALUES (?, ?, ?, ?, ?)",
                (chat_id, user_id, action, reason, _now_iso()),
            )
            self._conn.commit()

    # ---------------------------------------------------------- keyword hits
    def log_keyword_hit(self, chat_id: int, user_id: int, keyword: str,
                        text: str | None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO keyword_hits (chat_id, user_id, keyword, text, ts)"
                " VALUES (?, ?, ?, ?, ?)",
                (chat_id, user_id, keyword, (text or "")[:2000], _now_iso()),
            )
            self._conn.commit()

    # ----------------------------------------------------- runtime schedules
    def add_schedule(self, name: str, chat_id: int, cron: str, text: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO runtime_schedules (name, chat_id, cron, text, enabled)"
                " VALUES (?, ?, ?, ?, 1)",
                (name, chat_id, cron, text),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def delete_schedule(self, schedule_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM runtime_schedules WHERE id = ?", (schedule_id,)
            )
            self._conn.commit()
            return cur.rowcount > 0

    def list_schedules(self) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM runtime_schedules ORDER BY id"
            ).fetchall()

    # ------------------------------------------------------------------ meta
    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (key, value),
            )
            self._conn.commit()

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None

    # --------------------------------------------------------------- digest
    def message_counts_since(self, since_iso: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT chat_id, COUNT(*) AS n FROM messages"
                " WHERE ts >= ? GROUP BY chat_id ORDER BY n DESC",
                (since_iso,),
            ).fetchall()

    def new_members_since(self, since_iso: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT chat_id, user_id, username, joined_ts FROM new_members"
                " WHERE joined_ts >= ? ORDER BY joined_ts DESC",
                (since_iso,),
            ).fetchall()

    def mod_actions_since(self, since_iso: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT chat_id, user_id, action, reason, ts FROM mod_actions"
                " WHERE ts >= ? ORDER BY ts DESC",
                (since_iso,),
            ).fetchall()

    def keyword_hits_since(self, since_iso: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT chat_id, keyword, COUNT(*) AS n FROM keyword_hits"
                " WHERE ts >= ? GROUP BY chat_id, keyword ORDER BY n DESC",
                (since_iso,),
            ).fetchall()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
