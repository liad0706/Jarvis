"""Audit log — records every action with who requested it, what ran, and what changed."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

AUDIT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "audit.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    trace_id TEXT,
    actor TEXT NOT NULL,           -- 'user', 'llm', 'system'
    action TEXT NOT NULL,          -- e.g. 'code_run', 'appointment_book'
    skill TEXT,
    params_summary TEXT,           -- JSON, sensitive values redacted
    risk_level TEXT,               -- 'read', 'write', 'external', 'critical'
    approved_by TEXT,              -- 'auto', 'user', 'blocked'
    result_status TEXT,            -- 'ok', 'error', 'denied', 'dry_run'
    changes_summary TEXT,
    duration_ms REAL
);
"""


@dataclass
class AuditEntry:
    actor: str
    action: str
    skill: str = ""
    params_summary: dict | None = None
    risk_level: str = "read"
    trace_id: str = ""
    approved_by: str = "auto"
    result_status: str = "ok"
    changes_summary: str = ""
    duration_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)


class AuditLog:
    def __init__(self, db_path: Path = AUDIT_DB_PATH):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def log(self, entry: AuditEntry):
        if not self._db:
            return
        params_json = json.dumps(entry.params_summary or {}, ensure_ascii=False, default=str)
        await self._db.execute(
            """INSERT INTO audit_log
               (timestamp, trace_id, actor, action, skill, params_summary,
                risk_level, approved_by, result_status, changes_summary, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.timestamp,
                entry.trace_id,
                entry.actor,
                entry.action,
                entry.skill,
                params_json,
                entry.risk_level,
                entry.approved_by,
                entry.result_status,
                entry.changes_summary,
                entry.duration_ms,
            ),
        )
        await self._db.commit()

    async def query(
        self,
        skill: str | None = None,
        actor: str | None = None,
        risk_level: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        conditions = []
        params: list = []
        if skill:
            conditions.append("skill = ?")
            params.append(skill)
        if actor:
            conditions.append("actor = ?")
            params.append(actor)
        if risk_level:
            conditions.append("risk_level = ?")
            params.append(risk_level)

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"SELECT * FROM audit_log{where} ORDER BY id DESC LIMIT ?"
        params.append(limit)

        cursor = await self._db.execute(sql, params)
        columns = [d[0] for d in cursor.description]
        rows = await cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]

    async def export(self, fmt: str = "json") -> str:
        rows = await self.query(limit=10000)
        if fmt == "json":
            return json.dumps(rows, indent=2, ensure_ascii=False, default=str)
        lines = []
        for r in rows:
            lines.append(
                f"[{r['timestamp']}] {r['actor']} | {r['skill']}.{r['action']} "
                f"| risk={r['risk_level']} approved={r['approved_by']} "
                f"| result={r['result_status']}"
            )
        return "\n".join(lines)

    async def close(self):
        if self._db:
            await self._db.close()
