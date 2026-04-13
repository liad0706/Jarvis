"""Telemetry system — tracks latency, tokens, cost, and energy per LLM call.

Stores metrics in SQLite for historical analysis. Every inference call is
recorded with provider, model, token counts, latency, and estimated cost.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "telemetry.db"

# Cost per 1M tokens (approximate, update as pricing changes)
_COST_TABLE: dict[str, dict[str, float]] = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-5.4": {"input": 3.00, "output": 12.00},
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "claude-opus-4-20250514": {"input": 15.00, "output": 75.00},
    "ollama": {"input": 0.0, "output": 0.0},  # free (local)
}


@dataclass
class InferenceRecord:
    """One LLM inference call."""
    id: int = 0
    timestamp: float = 0.0
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    ttft_ms: float = 0.0  # time to first token (streaming)
    cost_usd: float = 0.0
    skill_name: str = ""
    success: bool = True
    error: str = ""


@dataclass
class TelemetryStats:
    """Aggregated stats for a time period."""
    total_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    error_rate: float = 0.0
    by_model: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_skill: dict[str, dict[str, Any]] = field(default_factory=dict)


class TelemetryCollector:
    """Collects and stores telemetry for all LLM calls."""

    def __init__(self, db_path: Path | str = DB_PATH):
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None
        self._buffer: list[InferenceRecord] = []
        self._flush_threshold = 10

    async def init(self):
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path))
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS inference_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                latency_ms REAL DEFAULT 0,
                ttft_ms REAL DEFAULT 0,
                cost_usd REAL DEFAULT 0,
                skill_name TEXT DEFAULT '',
                success INTEGER DEFAULT 1,
                error TEXT DEFAULT ''
            )
        """)
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_inference_ts ON inference_log(timestamp)
        """)
        await self._db.commit()
        logger.info("Telemetry: initialized at %s", self._db_path)

    def estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost in USD for a call."""
        # Check exact match, then prefix match
        costs = _COST_TABLE.get(model)
        if not costs:
            for key, val in _COST_TABLE.items():
                if model.startswith(key) or key.startswith(model.split(":")[0]):
                    costs = val
                    break
        if not costs:
            # Local models are free
            if any(kw in model.lower() for kw in ["qwen", "llama", "gemma", "phi"]):
                return 0.0
            return 0.0
        return (input_tokens * costs["input"] + output_tokens * costs["output"]) / 1_000_000

    async def record(
        self,
        provider: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: float = 0.0,
        ttft_ms: float = 0.0,
        skill_name: str = "",
        success: bool = True,
        error: str = "",
    ) -> InferenceRecord:
        """Record an inference call."""
        cost = self.estimate_cost(model, input_tokens, output_tokens)
        record = InferenceRecord(
            timestamp=time.time(),
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
            cost_usd=cost,
            skill_name=skill_name,
            success=success,
            error=error,
        )
        self._buffer.append(record)
        if len(self._buffer) >= self._flush_threshold:
            await self.flush()
        return record

    async def flush(self):
        """Flush buffered records to database."""
        if not self._buffer or not self._db:
            return
        records = self._buffer[:]
        self._buffer.clear()
        for r in records:
            await self._db.execute(
                """INSERT INTO inference_log
                   (timestamp, provider, model, input_tokens, output_tokens,
                    latency_ms, ttft_ms, cost_usd, skill_name, success, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (r.timestamp, r.provider, r.model, r.input_tokens, r.output_tokens,
                 r.latency_ms, r.ttft_ms, r.cost_usd, r.skill_name,
                 1 if r.success else 0, r.error),
            )
        await self._db.commit()

    async def get_stats(self, hours: float = 24) -> TelemetryStats:
        """Get aggregated stats for the last N hours."""
        if not self._db:
            return TelemetryStats()

        await self.flush()
        since = time.time() - (hours * 3600)

        cursor = await self._db.execute(
            """SELECT provider, model, input_tokens, output_tokens,
                      latency_ms, cost_usd, skill_name, success
               FROM inference_log WHERE timestamp > ?
               ORDER BY timestamp""",
            (since,),
        )
        rows = await cursor.fetchall()
        if not rows:
            return TelemetryStats()

        stats = TelemetryStats()
        latencies = []
        model_stats: dict[str, dict] = defaultdict(lambda: {
            "calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0, "errors": 0
        })
        skill_stats: dict[str, dict] = defaultdict(lambda: {
            "calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0
        })
        errors = 0

        for row in rows:
            provider, model, tin, tout, lat, cost, skill, success = row
            stats.total_calls += 1
            stats.total_input_tokens += tin
            stats.total_output_tokens += tout
            stats.total_cost_usd += cost
            latencies.append(lat)

            ms = model_stats[model]
            ms["calls"] += 1
            ms["tokens_in"] += tin
            ms["tokens_out"] += tout
            ms["cost"] += cost
            if not success:
                ms["errors"] += 1
                errors += 1

            if skill:
                ss = skill_stats[skill]
                ss["calls"] += 1
                ss["tokens_in"] += tin
                ss["tokens_out"] += tout
                ss["cost"] += cost

        if latencies:
            stats.avg_latency_ms = sum(latencies) / len(latencies)
            sorted_lat = sorted(latencies)
            p95_idx = int(len(sorted_lat) * 0.95)
            stats.p95_latency_ms = sorted_lat[min(p95_idx, len(sorted_lat) - 1)]

        stats.error_rate = errors / stats.total_calls if stats.total_calls else 0
        stats.by_model = dict(model_stats)
        stats.by_skill = dict(skill_stats)
        return stats

    async def get_today_summary(self) -> str:
        """Human-readable summary for today."""
        stats = await self.get_stats(hours=24)
        if stats.total_calls == 0:
            return "אין קריאות LLM ב-24 שעות האחרונות."

        lines = [
            f"📊 סיכום טלמטריה (24 שעות):",
            f"  קריאות: {stats.total_calls}",
            f"  טוקנים: {stats.total_input_tokens:,} in / {stats.total_output_tokens:,} out",
            f"  עלות: ${stats.total_cost_usd:.4f}",
            f"  Latency ממוצע: {stats.avg_latency_ms:.0f}ms | P95: {stats.p95_latency_ms:.0f}ms",
            f"  שגיאות: {stats.error_rate:.1%}",
        ]
        if stats.by_model:
            lines.append("  לפי מודל:")
            for model, ms in stats.by_model.items():
                lines.append(f"    {model}: {ms['calls']} calls, ${ms['cost']:.4f}")
        return "\n".join(lines)

    async def close(self):
        await self.flush()
        if self._db:
            await self._db.close()
