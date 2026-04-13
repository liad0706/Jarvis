"""Periodic progress summary for long-running tasks — adapted from Claude Code's agentSummary.ts.

Emits short progress summaries to the dashboard/event bus during multi-round
tool execution, so the user knows what's happening.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Minimum interval between progress summaries
SUMMARY_INTERVAL_SECONDS: float = 15.0


class ProgressTracker:
    """Tracks tool execution progress and emits periodic summaries."""

    def __init__(self, event_bus=None):
        self._event_bus = event_bus
        self._started_at: float = 0
        self._tools_executed: list[dict] = []
        self._last_summary_at: float = 0
        self._active: bool = False

    def start(self) -> None:
        self._started_at = time.time()
        self._tools_executed = []
        self._last_summary_at = self._started_at
        self._active = True

    def stop(self) -> None:
        self._active = False

    def record_tool(self, skill_name: str, action: str, success: bool, duration_ms: float = 0) -> None:
        """Record a tool execution for the current task."""
        self._tools_executed.append({
            "skill": skill_name,
            "action": action,
            "success": success,
            "duration_ms": duration_ms,
            "at": time.time(),
        })

    @property
    def elapsed_seconds(self) -> float:
        if not self._started_at:
            return 0
        return time.time() - self._started_at

    @property
    def tool_count(self) -> int:
        return len(self._tools_executed)

    def should_emit_summary(self) -> bool:
        """True if enough time has passed since the last summary."""
        if not self._active:
            return False
        return (time.time() - self._last_summary_at) >= SUMMARY_INTERVAL_SECONDS

    def build_summary(self) -> str:
        """Build a short progress summary string."""
        elapsed = self.elapsed_seconds
        count = self.tool_count
        if count == 0:
            return f"Working... ({elapsed:.0f}s elapsed)"

        recent = self._tools_executed[-3:]  # last 3 tools
        recent_names = [f"{t['skill']}.{t['action']}" for t in recent]
        failed = sum(1 for t in self._tools_executed if not t["success"])

        parts = [f"{count} tool{'s' if count != 1 else ''} executed ({elapsed:.0f}s)"]
        if failed:
            parts.append(f"{failed} failed")
        parts.append(f"recent: {', '.join(recent_names)}")

        return " | ".join(parts)

    async def maybe_emit_summary(self) -> str | None:
        """Emit a progress summary if enough time has passed. Returns the summary or None."""
        if not self.should_emit_summary():
            return None

        summary = self.build_summary()
        self._last_summary_at = time.time()

        if self._event_bus:
            await self._event_bus.emit("task.progress", summary=summary)

        logger.info("Progress: %s", summary)
        return summary

    def final_summary(self) -> str:
        """Build a final summary when the task completes."""
        elapsed = self.elapsed_seconds
        count = self.tool_count
        failed = sum(1 for t in self._tools_executed if not t["success"])
        unique_tools = list(dict.fromkeys(f"{t['skill']}.{t['action']}" for t in self._tools_executed))

        lines = [f"Task completed in {elapsed:.1f}s — {count} tool calls"]
        if failed:
            lines[0] += f" ({failed} failed)"
        if unique_tools:
            lines.append(f"Tools used: {', '.join(unique_tools[:10])}")
        return "\n".join(lines)
