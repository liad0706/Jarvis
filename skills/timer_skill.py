"""Timer/reminder skill — set async timers that fire after N minutes."""

from __future__ import annotations

import asyncio
import logging
import uuid
from time import monotonic
from typing import TYPE_CHECKING

from core.skill_base import BaseSkill

if TYPE_CHECKING:
    from core.event_bus import EventBus

logger = logging.getLogger(__name__)


class TimerSkill(BaseSkill):
    name = "timer"
    description = (
        "Set, list, and cancel countdown timers. "
        "When a timer fires it emits a notification with the original message."
    )

    RISK_MAP = {
        "set_timer": "low",
        "list_timers": "low",
        "cancel_timer": "low",
    }

    def __init__(self, event_bus: EventBus | None = None):
        self._event_bus = event_bus
        # timer_id -> {task, message, fire_at, minutes}
        self._timers: dict[str, dict] = {}

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        return await method(**(params or {}))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def do_set_timer(self, minutes: int = 1, message: str = "הטיימר הסתיים!") -> dict:
        """Set a countdown timer for N minutes. Fires an event with message when done."""
        if minutes <= 0:
            return {"error": "minutes חייב להיות מספר חיובי"}

        timer_id = uuid.uuid4().hex[:8]
        fire_at = monotonic() + minutes * 60

        task = asyncio.create_task(
            self._run_timer(timer_id, minutes, message),
            name=f"timer_{timer_id}",
        )
        self._timers[timer_id] = {
            "task": task,
            "message": message,
            "fire_at": fire_at,
            "minutes": minutes,
        }

        logger.info("Timer %s set for %d minute(s): %s", timer_id, minutes, message)
        return {
            "timer_id": timer_id,
            "minutes": minutes,
            "message": message,
            "reply_to_user_hebrew": f"⏱ הגדרתי טיימר ל-{minutes} דקות. מזהה: {timer_id}\nהודעה: {message}",
        }

    async def do_list_timers(self) -> dict:
        """List all active timers with time remaining."""
        # Clean up finished timers first
        finished = [tid for tid, t in self._timers.items() if t["task"].done()]
        for tid in finished:
            self._timers.pop(tid, None)

        if not self._timers:
            return {"reply_to_user_hebrew": "אין טיימרים פעילים כרגע."}

        now = monotonic()
        lines = []
        for tid, t in self._timers.items():
            remaining_secs = max(0.0, t["fire_at"] - now)
            mins_left = int(remaining_secs // 60)
            secs_left = int(remaining_secs % 60)
            lines.append(f"• [{tid}] — {mins_left}:{secs_left:02d} נותר — \"{t['message']}\"")

        return {
            "timers": [
                {
                    "id": tid,
                    "seconds_remaining": max(0.0, t["fire_at"] - now),
                    "message": t["message"],
                }
                for tid, t in self._timers.items()
            ],
            "reply_to_user_hebrew": "טיימרים פעילים:\n" + "\n".join(lines),
        }

    async def do_cancel_timer(self, timer_id: str = "") -> dict:
        """Cancel an active timer by its ID."""
        if not timer_id:
            return {"error": "חסר timer_id"}

        entry = self._timers.pop(timer_id, None)
        if entry is None:
            return {"reply_to_user_hebrew": f"לא נמצא טיימר עם מזהה: {timer_id}"}

        entry["task"].cancel()
        logger.info("Timer %s cancelled", timer_id)
        return {"reply_to_user_hebrew": f"הטיימר {timer_id} בוטל."}

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run_timer(self, timer_id: str, minutes: int, message: str) -> None:
        try:
            await asyncio.sleep(minutes * 60)
        except asyncio.CancelledError:
            logger.debug("Timer %s was cancelled", timer_id)
            return

        self._timers.pop(timer_id, None)
        logger.info("Timer %s fired: %s", timer_id, message)

        if self._event_bus:
            await self._event_bus.emit(
                "timer_fired",
                timer_id=timer_id,
                message=message,
                minutes=minutes,
            )
        else:
            print(f"\n⏰ טיימר {timer_id}: {message}\n")
