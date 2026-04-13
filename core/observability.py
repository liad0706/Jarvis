"""Observability layer: structured tracing, metrics collection, and correlated logging."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

METRICS_PATH = Path(__file__).resolve().parent.parent / "data" / "metrics.json"


@dataclass
class Span:
    name: str
    trace_id: str
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    parent_id: str | None = None
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"

    @property
    def duration_ms(self) -> float | None:
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time) * 1000

    def finish(self, status: str = "ok", **extra_metadata):
        self.end_time = time.time()
        self.status = status
        self.metadata.update(extra_metadata)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "metadata": self.metadata,
        }


class Trace:
    """Groups related spans under a single trace_id."""

    def __init__(self, trace_id: str | None = None):
        self.trace_id = trace_id or uuid.uuid4().hex[:16]
        self.spans: list[Span] = []
        self._span_stack: list[Span] = []

    @asynccontextmanager
    async def span(self, name: str, **metadata):
        parent_id = self._span_stack[-1].span_id if self._span_stack else None
        s = Span(name=name, trace_id=self.trace_id, parent_id=parent_id, metadata=metadata)
        self.spans.append(s)
        self._span_stack.append(s)
        try:
            yield s
        except Exception as exc:
            s.finish(status="error", error=str(exc))
            raise
        else:
            s.finish()
        finally:
            self._span_stack.pop()

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "spans": [s.to_dict() for s in self.spans],
        }


class MetricsCollector:
    """In-memory counters and histograms with periodic flush to disk."""

    def __init__(self):
        self._counters: dict[str, int] = {}
        self._histograms: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()

    async def increment(self, metric: str, value: int = 1):
        async with self._lock:
            self._counters[metric] = self._counters.get(metric, 0) + value

    async def histogram(self, metric: str, value: float):
        async with self._lock:
            self._histograms.setdefault(metric, []).append(value)

    async def get_summary(self) -> dict:
        async with self._lock:
            summary: dict[str, Any] = {"counters": dict(self._counters)}
            hist_summary = {}
            for name, values in self._histograms.items():
                if not values:
                    continue
                sorted_v = sorted(values)
                hist_summary[name] = {
                    "count": len(sorted_v),
                    "min": sorted_v[0],
                    "max": sorted_v[-1],
                    "avg": sum(sorted_v) / len(sorted_v),
                    "p50": sorted_v[len(sorted_v) // 2],
                    "p95": sorted_v[int(len(sorted_v) * 0.95)],
                }
            summary["histograms"] = hist_summary
            return summary

    async def flush(self):
        summary = await self.get_summary()
        summary["flushed_at"] = time.time()
        try:
            METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
            METRICS_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        except OSError as e:
            logger.warning("Failed to flush metrics: %s", e)

    async def reset(self):
        async with self._lock:
            self._counters.clear()
            self._histograms.clear()
