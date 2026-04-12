"""Recent activity tracker with short-window deduplication.

Keeps a compact record of what Jarvis has been doing without flooding the
dashboard or health endpoints with identical repeated events.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ActivityRecord:
    activity_type: str
    name: str
    detail: str = ""
    status: str = "ok"
    metadata: dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""
    count: int = 1
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "activity_type": self.activity_type,
            "name": self.name,
            "detail": self.detail,
            "status": self.status,
            "metadata": dict(self.metadata),
            "fingerprint": self.fingerprint,
            "count": self.count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ActivityManager:
    """Tracks recent activity and coalesces near-identical repeated events."""

    def __init__(self, dedup_window_seconds: float = 30.0, max_records: int = 200):
        self.dedup_window_seconds = dedup_window_seconds
        self.max_records = max_records
        self._records: list[ActivityRecord] = []
        self._recent_by_fingerprint: dict[str, ActivityRecord] = {}

    @staticmethod
    def _fingerprint_for(
        activity_type: str,
        name: str,
        detail: str,
        status: str,
        dedup_key: str = "",
    ) -> str:
        raw = dedup_key or f"{activity_type}|{name}|{status}|{detail}".strip().lower()
        return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]

    def record(
        self,
        activity_type: str,
        name: str,
        detail: str = "",
        status: str = "ok",
        metadata: dict[str, Any] | None = None,
        dedup_key: str = "",
    ) -> dict[str, Any]:
        now = time.time()
        fingerprint = self._fingerprint_for(activity_type, name, detail, status, dedup_key)
        existing = self._recent_by_fingerprint.get(fingerprint)

        if existing and (now - existing.updated_at) <= self.dedup_window_seconds:
            existing.count += 1
            existing.updated_at = now
            if metadata:
                existing.metadata.update(metadata)
            return existing.to_dict()

        record = ActivityRecord(
            activity_type=activity_type,
            name=name,
            detail=detail,
            status=status,
            metadata=dict(metadata or {}),
            fingerprint=fingerprint,
            created_at=now,
            updated_at=now,
        )
        self._records.append(record)
        self._recent_by_fingerprint[fingerprint] = record

        if len(self._records) > self.max_records:
            removed = self._records.pop(0)
            if self._recent_by_fingerprint.get(removed.fingerprint) is removed:
                self._recent_by_fingerprint.pop(removed.fingerprint, None)

        return record.to_dict()

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        return [record.to_dict() for record in self._records[-max(0, int(limit)):]]

    def clear(self) -> None:
        self._records.clear()
        self._recent_by_fingerprint.clear()

    @property
    def count(self) -> int:
        return len(self._records)
