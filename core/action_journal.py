"""
Action Journal — tracks every action Jarvis performs so the LLM
knows what it did recently.

In-memory ring buffer (last 50 entries) with daily persistence to
data/action_journal.json.  Auto-flushes every 5 minutes via an async
background task.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_BUFFER = 50
FLUSH_INTERVAL_SECONDS = 300  # 5 minutes
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
JOURNAL_PATH = DATA_DIR / "action_journal.json"

# Hebrew helpers for format_for_prompt
_ACTION_TYPE_HE = {
    "tool_call": "כלי",
    "routine": "שגרה",
    "proactive": "יוזמה",
    "discovery": "תגלית",
}


# ---------------------------------------------------------------------------
# Single entry dataclass-like dict builder
# ---------------------------------------------------------------------------
def _make_entry(
    action_type: str,
    action_name: str,
    params_summary: str,
    result_summary: str,
    success: bool,
) -> Dict[str, Any]:
    return {
        "timestamp": datetime.now().isoformat(),
        "action_type": action_type,
        "action_name": action_name,
        "params_summary": params_summary,
        "result_summary": result_summary,
        "success": success,
    }


# ---------------------------------------------------------------------------
# ActionJournal
# ---------------------------------------------------------------------------
class ActionJournal:
    """Ring-buffer journal with async disk persistence."""

    def __init__(self, journal_path: Optional[Path] = None) -> None:
        self._path = journal_path or JOURNAL_PATH
        self._buffer: Deque[Dict[str, Any]] = deque(maxlen=MAX_BUFFER)
        self._flush_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._dirty = False
        # Load persisted entries synchronously at init time (fast, small file)
        self._load_from_disk()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------
    def _load_from_disk(self) -> None:
        """Load existing journal from disk (called once at init)."""
        try:
            if self._path.exists():
                raw = self._path.read_text(encoding="utf-8")
                entries: List[Dict[str, Any]] = json.loads(raw) if raw.strip() else []
                # Only keep the most recent MAX_BUFFER entries
                for entry in entries[-MAX_BUFFER:]:
                    self._buffer.append(entry)
                log.info("ActionJournal: loaded %d entries from disk", len(self._buffer))
        except Exception:
            log.exception("ActionJournal: failed to load from disk")

    def _save_to_disk_sync(self, entries: List[Dict[str, Any]]) -> None:
        """Synchronous write — meant to be called via asyncio.to_thread."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(entries, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # Atomic-ish rename (Windows may need remove first)
            if self._path.exists():
                os.replace(str(tmp), str(self._path))
            else:
                tmp.rename(self._path)
        except Exception:
            log.exception("ActionJournal: failed to save to disk")

    async def flush(self) -> None:
        """Persist current buffer to disk."""
        async with self._lock:
            if not self._dirty:
                return
            entries = list(self._buffer)
            self._dirty = False
        await asyncio.to_thread(self._save_to_disk_sync, entries)
        log.debug("ActionJournal: flushed %d entries to disk", len(entries))

    # ------------------------------------------------------------------
    # Background auto-flush
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Start the background auto-flush loop."""
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._auto_flush_loop())
            log.info("ActionJournal: auto-flush task started")

    async def stop(self) -> None:
        """Stop background task and do a final flush."""
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        await self.flush()
        log.info("ActionJournal: stopped and flushed")

    async def _auto_flush_loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
            try:
                await self.flush()
            except Exception:
                log.exception("ActionJournal: auto-flush error")

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------
    def record(
        self,
        action_type: str,
        action_name: str,
        params_summary: str = "",
        result_summary: str = "",
        success: bool = True,
    ) -> Dict[str, Any]:
        """Record an action and return the entry."""
        entry = _make_entry(action_type, action_name, params_summary, result_summary, success)
        self._buffer.append(entry)
        self._dirty = True
        log.debug("ActionJournal: recorded %s / %s", action_type, action_name)
        return entry

    def get_recent(self, n: int = 10) -> List[Dict[str, Any]]:
        """Return the last *n* actions (most recent last)."""
        items = list(self._buffer)
        return items[-n:]

    def get_today(self) -> List[Dict[str, Any]]:
        """Return all actions from today."""
        today_str = datetime.now().strftime("%Y-%m-%d")
        items = list(self._buffer)
        return [e for e in items if e["timestamp"].startswith(today_str)]

    # ------------------------------------------------------------------
    # Hebrew prompt summary
    # ------------------------------------------------------------------
    def format_for_prompt(self, n: int = 10) -> str:
        """Return a concise Hebrew summary of recent actions for the LLM."""
        entries = self.get_recent(n)
        if not entries:
            return "=== פעולות אחרונות ===\n(אין פעולות אחרונות)"

        lines = ["=== פעולות אחרונות ==="]
        for e in entries:
            ts = datetime.fromisoformat(e["timestamp"])
            hhmm = ts.strftime("%H:%M")
            name = e["action_name"]
            ok = e["success"]

            # Build a short description
            detail = e.get("params_summary", "")
            result = e.get("result_summary", "")

            # Status suffix
            if ok:
                status = "הצליח ✅"
            else:
                status = "נכשל ❌"

            # Compose the line
            desc_parts = [name]
            if detail:
                desc_parts.append(detail)
            desc = " — ".join(desc_parts)

            if result:
                lines.append(f"• {hhmm} — {desc} ({status}: {result})")
            else:
                lines.append(f"• {hhmm} — {desc} ({status})")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Music / song helper
    # ------------------------------------------------------------------
    def get_songs_played_recently(self, days: int = 7) -> List[Dict[str, Any]]:
        """
        Return actions related to music/spotify from the last *days* days.
        Useful for avoiding song repetition.
        """
        cutoff = datetime.now() - timedelta(days=days)
        music_keywords = {"spotify", "music", "play_song", "play_music", "song", "שיר", "מוזיקה"}

        items = list(self._buffer)

        results: List[Dict[str, Any]] = []
        for e in items:
            try:
                ts = datetime.fromisoformat(e["timestamp"])
            except (ValueError, KeyError):
                continue
            if ts < cutoff:
                continue
            # Check name and params for music-related keywords
            searchable = (
                e.get("action_name", "").lower()
                + " "
                + e.get("params_summary", "").lower()
            )
            if any(kw in searchable for kw in music_keywords):
                results.append(e)

        return results
