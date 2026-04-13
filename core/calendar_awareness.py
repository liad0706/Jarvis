"""
Calendar/Schedule Awareness system for Jarvis.
Manages events, recurring schedules, and reminders with Hebrew locale support.
"""

import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CALENDAR_FILE = DATA_DIR / "calendar.json"

HEBREW_DAYS = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]

CATEGORY_LABELS = {
    "school": "בית ספר",
    "appointment": "תור",
    "personal": "אישי",
    "reminder": "תזכורת",
}

VALID_CATEGORIES = {"school", "appointment", "personal", "reminder"}
VALID_RECURRING = {"once", "daily", "weekly"}


class CalendarAwareness:
    def __init__(self, calendar_path: Optional[str] = None):
        self.calendar_path = Path(calendar_path) if calendar_path else CALENDAR_FILE
        self.events: list[dict] = []
        self._load()

    @staticmethod
    def _event_id() -> str:
        return f"evt_{uuid.uuid4().hex[:10]}"

    def _normalize_event(self, event: dict) -> tuple[dict, bool]:
        normalized = dict(event)
        changed = False
        if not normalized.get("id"):
            normalized["id"] = self._event_id()
            changed = True
        return normalized, changed

    # ------------------------------------------------------------------ IO
    def _load(self):
        if self.calendar_path.exists():
            try:
                with open(self.calendar_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                changed = False
                self.events = []
                for raw_event in loaded:
                    normalized, event_changed = self._normalize_event(raw_event)
                    self.events.append(normalized)
                    changed = changed or event_changed
                if changed:
                    self._save()
            except (json.JSONDecodeError, IOError):
                self.events = []
        else:
            self.events = []

    def _save(self):
        self.calendar_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.calendar_path, "w", encoding="utf-8") as f:
            json.dump(self.events, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _parse_date(date_str: str) -> datetime:
        return datetime.strptime(date_str, "%Y-%m-%d")

    @staticmethod
    def _parse_time(time_str: str) -> tuple[int, int]:
        parts = time_str.split(":")
        return int(parts[0]), int(parts[1])

    @staticmethod
    def _event_datetime(event: dict) -> datetime:
        """Return a datetime for the event's date + time."""
        dt = datetime.strptime(event["date"], "%Y-%m-%d")
        h, m = CalendarAwareness._parse_time(event["time"])
        return dt.replace(hour=h, minute=m)

    def _occurs_on(self, event: dict, target_date: datetime) -> bool:
        """Check whether *event* occurs on *target_date* (a date-only datetime)."""
        target_weekday = target_date.weekday()  # 0=Mon
        recurring = event.get("recurring", "once")
        event_date = self._parse_date(event["date"])

        if recurring == "once":
            return event_date.date() == target_date.date()

        if recurring == "daily":
            return target_date.date() >= event_date.date()

        if recurring == "weekly":
            recurring_days = event.get("recurring_days")
            if recurring_days is not None:
                return (
                    target_weekday in recurring_days
                    and target_date.date() >= event_date.date()
                )
            # No explicit days — fall back to same weekday as original date
            return (
                target_weekday == event_date.weekday()
                and target_date.date() >= event_date.date()
            )

        return False

    def _materialise(self, event: dict, target_date: datetime) -> dict:
        """Return a copy of *event* with its date set to *target_date*."""
        copy = dict(event)
        copy["date"] = target_date.strftime("%Y-%m-%d")
        return copy

    def _events_for_date(self, target: datetime) -> list[dict]:
        """All events that occur on *target* (date-only datetime)."""
        results = []
        for ev in self.events:
            if self._occurs_on(ev, target):
                results.append(self._materialise(ev, target))
        results.sort(key=lambda e: e.get("time", "00:00"))
        return results

    # --------------------------------------------------------- public API
    def add_event(
        self,
        title: str,
        date: str,
        time: str,
        end_time: Optional[str] = None,
        recurring: str = "once",
        recurring_days: Optional[list[int]] = None,
        category: str = "personal",
        reminder_minutes: int = 30,
    ) -> dict:
        if recurring not in VALID_RECURRING:
            raise ValueError(f"recurring must be one of {VALID_RECURRING}")
        if category not in VALID_CATEGORIES:
            raise ValueError(f"category must be one of {VALID_CATEGORIES}")
        # Validate date/time formats
        self._parse_date(date)
        self._parse_time(time)
        if end_time:
            self._parse_time(end_time)

        event: dict = {
            "id": self._event_id(),
            "title": title,
            "date": date,
            "time": time,
            "recurring": recurring,
            "category": category,
            "reminder_minutes": reminder_minutes,
        }
        if end_time:
            event["end_time"] = end_time
        if recurring_days is not None:
            event["recurring_days"] = recurring_days

        self.events.append(event)
        self._save()
        return event

    def remove_event(self, title: str, date: Optional[str] = None) -> bool:
        before = len(self.events)
        if date:
            self.events = [
                e
                for e in self.events
                if not (e["title"] == title and e["date"] == date)
            ]
        else:
            self.events = [
                e for e in self.events
                if e.get("id") != title and e["title"] != title
            ]
        removed = len(self.events) < before
        if removed:
            self._save()
        return removed

    def get_all_events(self) -> list[dict]:
        events = [dict(event) for event in self.events]
        return sorted(events, key=lambda e: (e.get("date", ""), e.get("time", "00:00")))

    def get_week(self, start_date: Optional[str] = None) -> list[dict]:
        if start_date:
            base = self._parse_date(start_date)
        else:
            now = datetime.now()
            base = now.replace(hour=0, minute=0, second=0, microsecond=0)
            base = base - timedelta(days=base.weekday())

        results = []
        for offset in range(7):
            target = base + timedelta(days=offset)
            results.extend(self._events_for_date(target))
        return sorted(results, key=lambda e: (e.get("date", ""), e.get("time", "00:00")))

    def get_today(self) -> list[dict]:
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return self._events_for_date(today)

    def get_tomorrow(self) -> list[dict]:
        tomorrow = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return self._events_for_date(tomorrow)

    def get_upcoming(self, hours: int = 24) -> list[dict]:
        now = datetime.now()
        end = now + timedelta(hours=hours)
        results = []

        # Check each date in the range
        current_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = end.replace(hour=0, minute=0, second=0, microsecond=0)
        while current_date <= end_date:
            for ev in self.events:
                if self._occurs_on(ev, current_date):
                    materialised = self._materialise(ev, current_date)
                    ev_dt = self._event_datetime(materialised)
                    if now <= ev_dt <= end:
                        results.append(materialised)
            current_date += timedelta(days=1)

        results.sort(key=lambda e: (e["date"], e.get("time", "00:00")))
        return results

    def get_reminders_due(self) -> list[dict]:
        now = datetime.now()
        due = []

        # Check today's events
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        # Also check tomorrow in case we're near midnight
        for check_date in [today, today + timedelta(days=1)]:
            for ev in self.events:
                if self._occurs_on(ev, check_date):
                    materialised = self._materialise(ev, check_date)
                    ev_dt = self._event_datetime(materialised)
                    reminder_min = materialised.get("reminder_minutes", 30)
                    reminder_start = ev_dt - timedelta(minutes=reminder_min)
                    if reminder_start <= now < ev_dt:
                        minutes_left = int((ev_dt - now).total_seconds() / 60)
                        materialised["_minutes_until"] = minutes_left
                        due.append(materialised)

        due.sort(key=lambda e: (e["date"], e.get("time", "00:00")))
        return due

    # ----------------------------------------------------------- display
    def _format_event_line(self, ev: dict) -> str:
        time_str = ev["time"]
        if "end_time" in ev:
            time_str += f"-{ev['end_time']}"
        cat_label = CATEGORY_LABELS.get(ev["category"], ev["category"])
        return f"• {time_str} — {ev['title']} ({cat_label})"

    def _hebrew_day(self, dt: datetime) -> str:
        return HEBREW_DAYS[dt.weekday()]

    def format_for_prompt(self) -> str:
        now = datetime.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = today + timedelta(days=1)

        lines = ["=== לוח זמנים ==="]

        # Today
        today_events = self._events_for_date(today)
        day_name = self._hebrew_day(today)
        date_str = today.strftime("%d/%m")
        lines.append(f"היום ({day_name} {date_str}):")
        if today_events:
            for ev in today_events:
                lines.append(self._format_event_line(ev))
        else:
            lines.append("• אין אירועים")
        lines.append("")

        # Tomorrow
        tomorrow_events = self._events_for_date(tomorrow)
        day_name = self._hebrew_day(tomorrow)
        date_str = tomorrow.strftime("%d/%m")
        lines.append(f"מחר ({day_name} {date_str}):")
        if tomorrow_events:
            for ev in tomorrow_events:
                lines.append(self._format_event_line(ev))
        else:
            lines.append("• אין אירועים")

        # Reminders
        reminders = self.get_reminders_due()
        if reminders:
            lines.append("")
            for r in reminders:
                mins = r.get("_minutes_until", "?")
                lines.append(f"⏰ תזכורת: {r['title']} מתחילה בעוד {mins} דקות!")

        return "\n".join(lines)
