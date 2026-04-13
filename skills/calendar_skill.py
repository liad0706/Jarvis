"""Calendar skill — lets the LLM manage User's schedule."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.skill_base import BaseSkill

if TYPE_CHECKING:
    from core.calendar_awareness import CalendarAwareness

logger = logging.getLogger(__name__)


class CalendarSkill(BaseSkill):
    name = "calendar"
    description = (
        "Manage User's calendar and schedule. "
        "Add/remove events, check today/tomorrow, see reminders."
    )

    RISK_MAP = {
        "add_event": "low",
        "remove_event": "low",
        "today": "low",
        "tomorrow": "low",
        "upcoming": "low",
    }

    def __init__(self, calendar: CalendarAwareness):
        self._cal = calendar

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        return await method(**(params or {}))

    async def do_add_event(
        self,
        title: str = "",
        date: str = "",
        time: str = "",
        end_time: str = "",
        recurring: str = "once",
        recurring_days: list[int] | None = None,
        category: str = "personal",
        reminder_minutes: int = 30,
    ) -> dict:
        """Add an event to the calendar. date format: YYYY-MM-DD, time: HH:MM."""
        if not title or not date or not time:
            return {"error": "חסר title, date, או time"}
        result = self._cal.add_event(
            title=title, date=date, time=time, end_time=end_time,
            recurring=recurring, recurring_days=recurring_days,
            category=category, reminder_minutes=reminder_minutes,
        )
        return {"reply_to_user_hebrew": f"הוספתי ליומן: {title} ב-{date} {time}", **result}

    async def do_remove_event(self, title: str = "", date: str = "") -> dict:
        """Remove an event by title (and optional date for one-off)."""
        if not title:
            return {"error": "חסר title"}
        ok = self._cal.remove_event(title, date or None)
        if ok:
            return {"reply_to_user_hebrew": f"הסרתי מהיומן: {title}"}
        return {"reply_to_user_hebrew": f"לא מצאתי ביומן: {title}"}

    async def do_today(self) -> dict:
        """Show today's events."""
        events = self._cal.get_today()
        if not events:
            return {"reply_to_user_hebrew": "אין אירועים היום ביומן."}
        lines = []
        for e in events:
            t = e.get("time", "?")
            end = f"-{e['end_time']}" if e.get("end_time") else ""
            lines.append(f"• {t}{end} — {e['title']}")
        return {"reply_to_user_hebrew": "אירועים היום:\n" + "\n".join(lines)}

    async def do_tomorrow(self) -> dict:
        """Show tomorrow's events."""
        events = self._cal.get_tomorrow()
        if not events:
            return {"reply_to_user_hebrew": "אין אירועים מחר ביומן."}
        lines = []
        for e in events:
            t = e.get("time", "?")
            end = f"-{e['end_time']}" if e.get("end_time") else ""
            lines.append(f"• {t}{end} — {e['title']}")
        return {"reply_to_user_hebrew": "אירועים מחר:\n" + "\n".join(lines)}

    async def do_upcoming(self, hours: int = 24) -> dict:
        """Show upcoming events in next N hours."""
        events = self._cal.get_upcoming(hours=hours)
        if not events:
            return {"reply_to_user_hebrew": f"אין אירועים בשעות הקרובות ({hours} שעות)."}
        lines = []
        for e in events:
            lines.append(f"• {e.get('time', '?')} — {e['title']}")
        return {"reply_to_user_hebrew": f"אירועים ב-{hours} שעות הקרובות:\n" + "\n".join(lines)}
