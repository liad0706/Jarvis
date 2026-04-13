"""Persistent task scheduler — runs routines at specific times.

Stores schedules in a JSON file. Runs as a background asyncio task
that checks every 30 seconds if any job should fire.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

RoutineFunc = Callable[..., Awaitable[dict[str, Any]]]

SCHEDULES_FILE = Path(__file__).resolve().parent.parent / "data" / "schedules.json"


class Schedule:
    """A single scheduled job."""

    def __init__(
        self,
        name: str,
        routine: str,
        hour: int,
        minute: int = 0,
        days: list[int] | None = None,
        enabled: bool = True,
        last_run: str = "",
    ):
        self.name = name
        self.routine = routine
        self.hour = hour
        self.minute = minute
        self.days = days  # 0=Mon..6=Sun; None = every day
        self.enabled = enabled
        self.last_run = last_run

    def should_run(self, now: datetime) -> bool:
        if not self.enabled:
            return False
        if self.days is not None and now.weekday() not in self.days:
            return False
        if now.hour != self.hour or now.minute != self.minute:
            return False
        today_key = now.strftime("%Y-%m-%d %H:%M")
        if self.last_run == today_key:
            return False
        return True

    def mark_ran(self, now: datetime) -> None:
        self.last_run = now.strftime("%Y-%m-%d %H:%M")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "routine": self.routine,
            "hour": self.hour,
            "minute": self.minute,
            "days": self.days,
            "enabled": self.enabled,
            "last_run": self.last_run,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Schedule:
        return cls(
            name=d["name"],
            routine=d["routine"],
            hour=d["hour"],
            minute=d.get("minute", 0),
            days=d.get("days"),
            enabled=d.get("enabled", True),
            last_run=d.get("last_run", ""),
        )


class Scheduler:
    """Background scheduler that fires registered routines at configured times."""

    def __init__(self) -> None:
        self._schedules: list[Schedule] = []
        self._routines: dict[str, RoutineFunc] = {}
        self._task: asyncio.Task | None = None
        self._running = False

    def register_routine(self, name: str, func: RoutineFunc) -> None:
        self._routines[name] = func
        logger.info("Scheduler: registered routine '%s'", name)

    def load(self) -> None:
        if SCHEDULES_FILE.exists():
            try:
                data = json.loads(SCHEDULES_FILE.read_text(encoding="utf-8"))
                self._schedules = [Schedule.from_dict(s) for s in data]
                logger.info("Scheduler: loaded %d schedule(s)", len(self._schedules))
                return
            except Exception as e:
                logger.warning("Scheduler: failed to load schedules: %s", e)
        self._schedules = []

    def save(self) -> None:
        SCHEDULES_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = [s.to_dict() for s in self._schedules]
        SCHEDULES_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def add_schedule(self, schedule: Schedule) -> None:
        for i, existing in enumerate(self._schedules):
            if existing.name == schedule.name:
                self._schedules[i] = schedule
                self.save()
                return
        self._schedules.append(schedule)
        self.save()

    def remove_schedule(self, name: str) -> bool:
        before = len(self._schedules)
        self._schedules = [s for s in self._schedules if s.name != name]
        if len(self._schedules) < before:
            self.save()
            return True
        return False

    def list_schedules(self) -> list[dict]:
        return [s.to_dict() for s in self._schedules]

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Scheduler: background loop started")

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Scheduler: stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                now = datetime.now()
                for schedule in self._schedules:
                    if schedule.should_run(now):
                        routine = self._routines.get(schedule.routine)
                        if routine:
                            logger.info(
                                "Scheduler: firing '%s' (routine: %s)",
                                schedule.name,
                                schedule.routine,
                            )
                            schedule.mark_ran(now)
                            self.save()
                            asyncio.create_task(
                                self._run_safe(schedule.name, routine)
                            )
                        else:
                            logger.warning(
                                "Scheduler: routine '%s' not registered (schedule: %s)",
                                schedule.routine,
                                schedule.name,
                            )
            except Exception as e:
                logger.exception("Scheduler loop error: %s", e)

            await asyncio.sleep(30)

    async def _run_safe(self, name: str, routine: RoutineFunc) -> None:
        try:
            result = await routine()
            logger.info("Scheduler: '%s' completed: %s", name, result)
        except Exception as e:
            logger.exception("Scheduler: '%s' failed: %s", name, e)

    async def run_now(self, routine_name: str) -> dict[str, Any]:
        """Manually trigger a routine by name (for testing / CLI)."""
        routine = self._routines.get(routine_name)
        if not routine:
            return {"error": f"Routine '{routine_name}' not registered"}
        return await routine()
