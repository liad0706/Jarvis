"""Network presence skill — lets the LLM check who is home."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.skill_base import BaseSkill

if TYPE_CHECKING:
    from core.network_presence import NetworkPresence

logger = logging.getLogger(__name__)


class PresenceSkill(BaseSkill):
    name = "presence"
    description = (
        "Scan the local WiFi network to see all connected devices and who is home. "
        "Use 'scan' to list every device on the network. "
        "Use 'is_home' to check a specific person. "
        "Use 'register_device' to name an unknown device. "
        "Triggers: wifi, רשת, מחוברים, מי בבית, מי מחובר, connected devices."
    )

    RISK_MAP = {
        "scan": "low",
        "scan_all": "low",
        "is_home": "low",
        "register_device": "low",
        "list_devices": "low",
    }

    def __init__(self, presence: NetworkPresence):
        self._presence = presence

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        return await method(**(params or {}))

    async def do_scan(self) -> dict:
        """Scan the WiFi network and report all connected devices + who is home."""
        result = await self._presence.scan()
        text = self._presence.format_for_prompt(result)
        return {"reply_to_user_hebrew": text, **result}

    async def do_scan_all(self) -> dict:
        """Scan and return only the full device list (all connected devices)."""
        result = await self._presence.scan()
        text = self._presence.format_for_prompt(result)
        return {"reply_to_user_hebrew": text, **result}

    async def do_is_home(self, owner: str = "") -> dict:
        """Check if a specific person is home."""
        if not owner:
            return {"error": "חסר owner"}
        home = await self._presence.is_home(owner)
        name = owner
        if home:
            return {"reply_to_user_hebrew": f"{name} בבית 🏠", "home": True}
        return {"reply_to_user_hebrew": f"{name} לא בבית", "home": False}

    async def do_register_device(
        self, name: str = "", mac: str = "", ip: str = "", owner: str = ""
    ) -> dict:
        """Register a device to track on the network."""
        if not name or not owner:
            return {"error": "חסר name או owner"}
        self._presence.register_device(name=name, mac=mac, ip=ip, owner=owner)
        return {"reply_to_user_hebrew": f"רשמתי את {name} ({owner})"}

    async def do_list_devices(self) -> dict:
        """List all known tracked devices."""
        devices = self._presence.list_devices()
        if not devices:
            return {"reply_to_user_hebrew": "אין מכשירים רשומים. תרשום עם register_device."}
        lines = []
        for d in devices:
            lines.append(f"• {d['name']} — {d.get('owner', '?')} (IP: {d.get('ip', '?')})")
        return {"reply_to_user_hebrew": "מכשירים רשומים:\n" + "\n".join(lines)}
