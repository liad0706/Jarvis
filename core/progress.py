"""Real-time progress reporting via EventBus — shows users what Jarvis is doing."""

from __future__ import annotations

import sys
import logging
from typing import Any

from core.event_bus import EventBus

logger = logging.getLogger(__name__)


class ProgressReporter:
    """Subscribes to EventBus events and prints human-friendly status updates."""

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self._subscribe()

    def _subscribe(self):
        self.event_bus.on("llm.start", self._on_llm_start)
        self.event_bus.on("llm.complete", self._on_llm_complete)
        self.event_bus.on("tool.start", self._on_tool_start)
        self.event_bus.on("tool.complete", self._on_tool_complete)
        self.event_bus.on("task.progress", self._on_task_progress)
        self.event_bus.on("plan.deciding", self._on_plan_deciding)
        self.event_bus.on("plan.created", self._on_plan_created)
        self.event_bus.on("plan.start", self._on_plan_start)
        self.event_bus.on("step.start", self._on_step_start)
        self.event_bus.on("step.complete", self._on_step_complete)
        self.event_bus.on("step.failed", self._on_step_failed)
        self.event_bus.on("step.retry", self._on_step_retry)

    # --- LLM events ---

    async def _on_llm_start(self, **kwargs):
        round_num = kwargs.get("round", 0)
        if round_num == 0:
            if kwargs.get("slow_vl_model"):
                _status(
                    "מחכה ל-Olama (מודל ראייה כבד — טעינה ראשונה עד כמה דקות; אחר כך מהיר יותר)…"
                )
            else:
                _status("Thinking...")
        else:
            _status(f"Thinking (round {round_num + 1})...")

    async def _on_llm_complete(self, **kwargs):
        pass

    # --- Tool events ---

    async def _on_tool_start(self, **kwargs):
        tool = kwargs.get("tool", "unknown")
        parts = tool.split("_", 1)
        if len(parts) == 2:
            skill, action = parts
            _status(f"Running: {skill} -> {action}")
        else:
            _status(f"Running: {tool}")

    async def _on_tool_complete(self, **kwargs):
        tool = kwargs.get("tool", "unknown")
        has_error = kwargs.get("has_error", False)
        if has_error:
            _status(f"Tool {tool} encountered an error")

    async def _on_task_progress(self, **kwargs):
        summary = kwargs.get("summary", "")
        if summary:
            _status(summary)

    # --- Planning events ---

    async def _on_plan_deciding(self, **kwargs):
        _status("Analyzing task complexity...")

    async def _on_plan_created(self, **kwargs):
        plan = kwargs.get("plan", {})
        steps = plan.get("steps", [])
        _status(f"Created plan with {len(steps)} steps")

    async def _on_plan_start(self, **kwargs):
        goal = kwargs.get("goal", "")
        total = kwargs.get("total_steps", 0)
        _status(f"Starting task: {goal[:50]}... ({total} steps)")

    async def _on_step_start(self, **kwargs):
        current = kwargs.get("current", "?")
        total = kwargs.get("total", "?")
        desc = kwargs.get("description", "")
        _step(current, total, desc)

    async def _on_step_complete(self, **kwargs):
        current = kwargs.get("current", "?")
        total = kwargs.get("total", "?")
        _status(f"Step {current}/{total} complete")

    async def _on_step_failed(self, **kwargs):
        step_id = kwargs.get("step_id", "?")
        error = kwargs.get("error", "unknown")
        _error(f"Step {step_id} failed: {error[:80]}")

    async def _on_step_retry(self, **kwargs):
        step_id = kwargs.get("step_id", "?")
        attempt = kwargs.get("attempt", "?")
        _status(f"Retrying step {step_id} (attempt {attempt})...")


def _status(message: str):
    sys.stdout.write(f"\r\033[90m  [...] {message}\033[0m\033[K\n")
    sys.stdout.flush()


def _step(current, total, description: str):
    sys.stdout.write(f"\r\033[96m  [{current}/{total}]\033[0m {description}\033[K\n")
    sys.stdout.flush()


def _error(message: str):
    sys.stdout.write(f"\r\033[91m  [!] {message}\033[0m\033[K\n")
    sys.stdout.flush()
