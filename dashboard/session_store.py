"""Persistent chat session store — SQLite-backed, keeps conversations across restarts.

Each session has a title, timestamps, and a list of messages (transcript + conv).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "chat_sessions.db"


class SessionStore:
    """SQLite-backed chat session persistence."""

    def __init__(self, db_path: Path | str | None = None):
        self._db_path = Path(db_path) if db_path else DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_tables()

    def _init_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL DEFAULT 'שיחה חדשה',
                created_at  REAL NOT NULL,
                updated_at  REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                role        TEXT NOT NULL,
                content     TEXT NOT NULL DEFAULT '',
                msg_type    TEXT NOT NULL DEFAULT 'text',
                url         TEXT,
                created_at  REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    def create_session(self, session_id: str, title: str = "שיחה חדשה") -> dict:
        now = time.time()
        self._conn.execute(
            "INSERT OR IGNORE INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (session_id, title, now, now),
        )
        self._conn.commit()
        return {"id": session_id, "title": title, "created_at": now, "updated_at": now}

    def update_title(self, session_id: str, title: str):
        self._conn.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, time.time(), session_id),
        )
        self._conn.commit()

    def touch(self, session_id: str):
        self._conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (time.time(), session_id),
        )
        self._conn.commit()

    def delete_session(self, session_id: str):
        self._conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self._conn.commit()

    def list_sessions(self) -> list[dict]:
        """All sessions, newest first."""
        rows = self._conn.execute(
            "SELECT s.id, s.title, s.created_at, s.updated_at, "
            "  (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count "
            "FROM sessions s ORDER BY s.updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def session_exists(self, session_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return row is not None

    def get_session_meta(self, session_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT id, title, created_at, updated_at FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def add_message(self, session_id: str, role: str, content: str,
                    msg_type: str = "text", url: str | None = None):
        """Append a message and touch the session."""
        now = time.time()
        # Auto-create session if needed
        if not self.session_exists(session_id):
            self.create_session(session_id)
        self._conn.execute(
            "INSERT INTO messages (session_id, role, content, msg_type, url, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, role, content, msg_type, url, now),
        )
        self._conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
        )
        self._conn.commit()

    def get_transcript(self, session_id: str, limit: int = 200) -> list[dict]:
        """Get recent messages for a session (for UI display)."""
        rows = self._conn.execute(
            "SELECT role, content, msg_type, url, created_at FROM messages "
            "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        # Reverse to chronological order
        result = []
        for r in reversed(rows):
            entry: dict[str, Any] = {"role": r["role"], "content": r["content"]}
            if r["msg_type"] != "text":
                entry["type"] = r["msg_type"]
            if r["url"]:
                entry["url"] = r["url"]
            result.append(entry)
        return result

    def get_conv(self, session_id: str, limit: int = 80) -> list[dict]:
        """Get messages in LLM conversation format (role + content only, text messages)."""
        rows = self._conn.execute(
            "SELECT role, content FROM messages "
            "WHERE session_id = ? AND msg_type = 'text' AND content != '' "
            "ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def get_message_count(self, session_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # Bulk load (startup)
    # ------------------------------------------------------------------

    def load_all_sessions(self) -> dict[str, dict[str, Any]]:
        """Load all sessions with their transcripts and conv lists into memory format
        compatible with dashboard server's _chat_sessions."""
        sessions = self.list_sessions()
        result: dict[str, dict[str, Any]] = {}
        for s in sessions:
            sid = s["id"]
            transcript = self.get_transcript(sid)
            conv = self.get_conv(sid)
            result[sid] = {
                "title": s["title"],
                "transcript": transcript,
                "conv": conv,
                "updated_at": s["updated_at"],
            }
        return result

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass
