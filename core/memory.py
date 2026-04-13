"""SQLite-based conversation memory with episodic memory support."""

import json
import logging
import time
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "memory.db"


class Memory:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                decision TEXT NOT NULL,
                reasoning TEXT,
                source_conversation_id INTEGER,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_start_id INTEGER,
                conversation_end_id INTEGER,
                summary TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT NOT NULL,
                source_id INTEGER,
                content TEXT,
                embedding BLOB,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS episodic_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_type TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT,
                created_at REAL NOT NULL,
                recalled_count INTEGER DEFAULT 0,
                last_recalled_at REAL
            );
            """
        )
        await self._db.commit()

    # ── Conversations ──

    async def add_message(self, role: str, content: str) -> None:
        await self._db.execute(
            "INSERT INTO conversations (role, content, timestamp) VALUES (?, ?, ?)",
            (role, content, time.time()),
        )
        await self._db.commit()

    async def get_recent_messages(self, limit: int = 20) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT role, content FROM conversations ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

    # ── Facts ──

    async def set_fact(self, key: str, value: str) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO facts (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, time.time()),
        )
        await self._db.commit()

    async def get_fact(self, key: str) -> str | None:
        cursor = await self._db.execute(
            "SELECT value FROM facts WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def get_all_facts(self) -> dict[str, str]:
        cursor = await self._db.execute("SELECT key, value FROM facts")
        rows = await cursor.fetchall()
        return {r[0]: r[1] for r in rows}

    async def remove_fact(self, key: str) -> bool:
        cursor = await self._db.execute("DELETE FROM facts WHERE key = ?", (key,))
        await self._db.commit()
        return cursor.rowcount > 0

    # ── Episodic Memories ──

    async def add_episodic_memory(
        self,
        memory_type: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Insert an episodic memory and return its id."""
        meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        cursor = await self._db.execute(
            """INSERT INTO episodic_memories
               (memory_type, content, metadata, created_at)
               VALUES (?, ?, ?, ?)""",
            (memory_type, content, meta_json, time.time()),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_episodic_memories(
        self,
        memory_type: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        if memory_type:
            cursor = await self._db.execute(
                """SELECT id, memory_type, content, metadata, created_at,
                          recalled_count, last_recalled_at
                   FROM episodic_memories
                   WHERE memory_type = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (memory_type, limit),
            )
        else:
            cursor = await self._db.execute(
                """SELECT id, memory_type, content, metadata, created_at,
                          recalled_count, last_recalled_at
                   FROM episodic_memories
                   ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "memory_type": r[1],
                "content": r[2],
                "metadata": json.loads(r[3]) if r[3] else None,
                "created_at": r[4],
                "recalled_count": r[5],
                "last_recalled_at": r[6],
            }
            for r in rows
        ]

    async def delete_episodic_memory(self, memory_id: int) -> bool:
        cursor = await self._db.execute(
            "DELETE FROM episodic_memories WHERE id = ?", (memory_id,)
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def update_recall_stats(self, memory_id: int) -> None:
        await self._db.execute(
            """UPDATE episodic_memories
               SET recalled_count = recalled_count + 1, last_recalled_at = ?
               WHERE id = ?""",
            (time.time(), memory_id),
        )
        await self._db.commit()

    async def prune(
        self,
        conversations_keep_days: int = 30,
        episodic_keep_days: int = 90,
        embeddings_keep_days: int = 90,
    ) -> dict:
        """Delete old rows to keep the database from growing unbounded."""
        now = time.time()
        conv_cutoff = now - conversations_keep_days * 86400
        epis_cutoff = now - episodic_keep_days * 86400
        emb_cutoff = now - embeddings_keep_days * 86400

        c1 = await self._db.execute(
            "DELETE FROM conversations WHERE timestamp < ?", (conv_cutoff,)
        )
        c2 = await self._db.execute(
            "DELETE FROM episodic_memories WHERE created_at < ? AND (last_recalled_at IS NULL OR last_recalled_at < ?)",
            (epis_cutoff, epis_cutoff),
        )
        c3 = await self._db.execute(
            "DELETE FROM embeddings WHERE created_at < ?", (emb_cutoff,)
        )
        await self._db.commit()
        result = {
            "conversations_deleted": c1.rowcount,
            "episodic_deleted": c2.rowcount,
            "embeddings_deleted": c3.rowcount,
        }
        logger.info("Memory pruned: %s", result)
        return result

    async def close(self) -> None:
        if self._db:
            await self._db.close()
