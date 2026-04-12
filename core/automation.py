"""Automation engine — trigger-based action chains (IFTTT-style).

Examples:
  - When presence detected (someone comes home) → turn on lights + play music
  - When school time → mute notifications
  - When 3D print finishes → send WhatsApp notification
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

AUTOMATIONS_FILE = Path(__file__).resolve().parent.parent / "data" / "automations.json"


@dataclass
class AutomationAction:
    """A single action in an automation chain."""
    skill_name: str
    action_name: str
    params: dict = field(default_factory=dict)
    delay_seconds: float = 0  # delay before executing

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> AutomationAction:
        return cls(**d)


@dataclass
class Automation:
    """A complete automation rule: trigger → condition → actions."""
    name: str
    trigger_event: str  # EventBus event name to listen for
    actions: list[AutomationAction] = field(default_factory=list)
    conditions: dict = field(default_factory=dict)  # key-value conditions on event kwargs
    enabled: bool = True
    last_triggered: str = ""
    cooldown_seconds: float = 60  # minimum time between triggers
    description: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["actions"] = [a.to_dict() for a in self.actions]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Automation:
        actions = [AutomationAction.from_dict(a) for a in d.get("actions", [])]
        return cls(
            name=d["name"],
            trigger_event=d["trigger_event"],
            actions=actions,
            conditions=d.get("conditions", {}),
            enabled=d.get("enabled", True),
            last_triggered=d.get("last_triggered", ""),
            cooldown_seconds=d.get("cooldown_seconds", 60),
            description=d.get("description", ""),
        )


class AutomationEngine:
    """Listens for EventBus events and runs automation chains."""

    def __init__(self, event_bus=None, registry=None, notifications=None):
        self.event_bus = event_bus
        self.registry = registry  # SkillRegistry for executing actions
        self.notifications = notifications  # NotificationManager
        self._automations: list[Automation] = []
        self._subscribed_events: set[str] = set()
        self.load()

    def load(self):
        """Load automations from disk."""
        if AUTOMATIONS_FILE.exists():
            try:
                data = json.loads(AUTOMATIONS_FILE.read_text(encoding="utf-8"))
                self._automations = [Automation.from_dict(a) for a in data]
                logger.info("Loaded %d automation(s)", len(self._automations))
            except Exception as e:
                logger.warning("Failed to load automations: %s", e)

    def save(self):
        """Save automations to disk."""
        AUTOMATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = [a.to_dict() for a in self._automations]
        AUTOMATIONS_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def add_automation(self, automation: Automation) -> None:
        """Add or update an automation."""
        # Replace if same name exists
        self._automations = [a for a in self._automations if a.name != automation.name]
        self._automations.append(automation)
        self.save()
        self._subscribe(automation.trigger_event)
        logger.info("Automation added: %s (trigger: %s)", automation.name, automation.trigger_event)

    def remove_automation(self, name: str) -> bool:
        before = len(self._automations)
        self._automations = [a for a in self._automations if a.name != name]
        if len(self._automations) < before:
            self.save()
            return True
        return False

    def list_automations(self) -> list[dict]:
        return [a.to_dict() for a in self._automations]

    def get_automation(self, name: str) -> Automation | None:
        for a in self._automations:
            if a.name == name:
                return a
        return None

    def _subscribe(self, event: str):
        """Subscribe to an EventBus event if not already subscribed."""
        if event not in self._subscribed_events and self.event_bus:
            async def _handler(**kwargs):
                await self._on_event(event, kwargs)
            self.event_bus.on(event, _handler)
            self._subscribed_events.add(event)

    def subscribe_all(self):
        """Subscribe to all events from loaded automations."""
        for auto in self._automations:
            if auto.enabled:
                self._subscribe(auto.trigger_event)

    async def _on_event(self, event: str, kwargs: dict):
        """Handle an event — check conditions and run matching automations."""
        now = time.time()
        for auto in self._automations:
            if not auto.enabled or auto.trigger_event != event:
                continue

            # Check cooldown
            if auto.last_triggered:
                try:
                    from datetime import datetime
                    last = datetime.fromisoformat(auto.last_triggered).timestamp()
                    if now - last < auto.cooldown_seconds:
                        continue
                except (ValueError, TypeError):
                    pass

            # Check conditions
            if auto.conditions:
                match = all(
                    kwargs.get(k) == v for k, v in auto.conditions.items()
                )
                if not match:
                    continue

            # Run actions
            logger.info("Automation triggered: %s (event: %s)", auto.name, event)
            auto.last_triggered = time.strftime("%Y-%m-%dT%H:%M:%S")
            self.save()

            asyncio.create_task(self._run_actions(auto))

    async def _run_actions(self, automation: Automation):
        """Execute all actions in an automation chain."""
        for action in automation.actions:
            try:
                if action.delay_seconds > 0:
                    await asyncio.sleep(action.delay_seconds)

                if self.registry:
                    skill = self.registry.get(action.skill_name)
                    if skill:
                        result = await skill.execute(action.action_name, action.params)
                        logger.info(
                            "Automation %s: %s.%s -> %s",
                            automation.name, action.skill_name,
                            action.action_name, result.get("status", "done"),
                        )
                    else:
                        logger.warning("Automation %s: skill '%s' not found", automation.name, action.skill_name)
            except Exception as e:
                logger.exception("Automation %s action failed: %s", automation.name, e)

        # Notify completion
        if self.notifications:
            await self.notifications.notify(
                title=f"אוטומציה: {automation.name}",
                message=f"הושלמה בהצלחה ({len(automation.actions)} פעולות)",
                source="automation",
            )
