"""Scheduler skill — lets the LLM view and manage scheduled tasks."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.skill_base import BaseSkill

if TYPE_CHECKING:
    from core.scheduler import Scheduler

logger = logging.getLogger(__name__)


class SchedulerSkill(BaseSkill):
    name = "scheduler"
    description = (
        "Manage scheduled/automated tasks (list schedules, run routines manually, "
        "enable/disable schedules, change times). "
        "Use this when the user asks about scheduled tasks, automations, morning routine timing, etc."
    )

    RISK_MAP = {
        "list": "low",
        "run": "medium",
        "enable": "medium",
        "disable": "medium",
        "set_time": "medium",
    }

    def __init__(self, scheduler: Scheduler):
        self._scheduler = scheduler

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        try:
            return await method(**(params or {}))
        except Exception as e:
            logger.exception("scheduler.%s failed", action)
            return {"error": str(e)}

    async def do_list(self) -> dict:
        """List all scheduled tasks with their times, days, and status."""
        schedules = self._scheduler.list_schedules()
        if not schedules:
            return {
                "status": "empty",
                "reply_to_user_hebrew": "אין משימות מתוזמנות כרגע.",
            }

        DAY_NAMES = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]
        items = []
        for s in schedules:
            if s["days"] is None:
                days_str = "כל יום"
            else:
                days_str = ", ".join(DAY_NAMES[d] for d in s["days"])
            enabled = "פעיל" if s["enabled"] else "מושבת"
            items.append(
                f"• {s['name']} — {s['routine']} ב-{s['hour']:02d}:{s['minute']:02d} ({days_str}) [{enabled}]"
            )

        summary = "\n".join(items)
        return {
            "status": "ok",
            "count": len(schedules),
            "schedules": schedules,
            "reply_to_user_hebrew": f"משימות מתוזמנות:\n{summary}",
        }

    async def do_run(self, routine: str = "morning_routine") -> dict:
        """Run a scheduled routine manually right now. Default: morning_routine."""
        result = await self._scheduler.run_now(routine)
        if "error" in result:
            return result
        return {
            "status": "ok",
            "result": result,
            "reply_to_user_hebrew": result.get("summary", f"הרצתי את {routine} בהצלחה."),
        }

    async def do_enable(self, name: str = "morning") -> dict:
        """Enable a scheduled task by name."""
        for s in self._scheduler._schedules:
            if s.name == name:
                s.enabled = True
                self._scheduler.save()
                return {
                    "status": "ok",
                    "reply_to_user_hebrew": f"המשימה '{name}' הופעלה.",
                }
        return {"error": f"Schedule '{name}' not found"}

    async def do_disable(self, name: str = "morning") -> dict:
        """Disable a scheduled task by name (keeps it but won't run)."""
        for s in self._scheduler._schedules:
            if s.name == name:
                s.enabled = False
                self._scheduler.save()
                return {
                    "status": "ok",
                    "reply_to_user_hebrew": f"המשימה '{name}' הושבתה.",
                }
        return {"error": f"Schedule '{name}' not found"}

    async def do_set_time(self, name: str = "morning", hour: int = 11, minute: int = 0) -> dict:
        """Change the time of a scheduled task."""
        for s in self._scheduler._schedules:
            if s.name == name:
                s.hour = int(hour)
                s.minute = int(minute)
                self._scheduler.save()
                return {
                    "status": "ok",
                    "reply_to_user_hebrew": f"המשימה '{name}' עודכנה ל-{int(hour):02d}:{int(minute):02d}.",
                }
        return {"error": f"Schedule '{name}' not found"}
