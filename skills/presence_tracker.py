"""Unified presence tracker — phone GPS + WiFi sensing + network ping.

Sources:
1. Home Assistant device trackers (iPhone GPS — home/away + location)
2. RuView WiFi sensing (CSI-based room presence, if available)
3. Network ping (detect phones on local WiFi — no app needed)
"""

import asyncio
import logging
import platform
import subprocess
import time
from typing import Any

import httpx

from config import get_settings
from core.skill_base import BaseSkill

logger = logging.getLogger(__name__)

# Family members tracked by network ping (IP -> name)
# Add phones here — when they connect to home WiFi, they're "home"
PING_TRACKED = {
    "10.0.0.1": "אמא",
}



class PresenceTrackerSkill(BaseSkill):
    name = "presence_tracker"
    description = (
        "Track where family members are — home/away via phone GPS, "
        "room-level presence via WiFi sensing."
    )

    RISK_MAP = {
        "who_is_home": "low",
        "where_is_everyone": "low",
        "family_status": "low",
    }

    def __init__(self, registry=None):
        s = get_settings()
        self.ha_url = s.ha_url.rstrip("/")
        self.ha_token = s.ha_token
        self._ha_headers = {
            "Authorization": f"Bearer {self.ha_token}",
            "Content-Type": "application/json",
        }
        self._registry = registry
        self._ha_retry_after: float = 0.0
        self._HA_COOLDOWN = 30.0
        self._ha_warned: bool = False

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    async def _ha_get(self, path: str) -> dict | list | None:
        if time.monotonic() < self._ha_retry_after:
            return None
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get(f"{self.ha_url}{path}", headers=self._ha_headers, timeout=10)
                r.raise_for_status()
                self._ha_retry_after = 0.0
                return r.json()
        except Exception as e:
            self._ha_retry_after = time.monotonic() + self._HA_COOLDOWN
            if not self._ha_warned:
                self._ha_warned = True
                logger.warning("HA GET %s failed: %s (further warnings suppressed)", path, e)
            return None

    # ------------------------------------------------------------------
    # Skill interface
    # ------------------------------------------------------------------

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        try:
            return await method(**(params or {}))
        except Exception as e:
            logger.exception("presence_tracker.%s failed", action)
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def do_who_is_home(self) -> dict:
        """Check who is home and who is away. Uses phone GPS tracking."""
        members = await self._get_family_members()
        home = [m for m in members if m["home"]]
        away = [m for m in members if not m["home"]]

        return {
            "status": "ok",
            "home_count": len(home),
            "away_count": len(away),
            "home": home,
            "away": away,
            "reply_to_user_hebrew": self._format_who_home(home, away),
        }

    async def do_where_is_everyone(self) -> dict:
        """Detailed location of all family members — GPS + battery."""
        members = await self._get_family_members()
        return {
            "status": "ok",
            "members": members,
            "reply_to_user_hebrew": self._format_where(members),
        }

    async def do_family_status(self) -> dict:
        """Full status: phone tracking + WiFi room sensing."""
        members = await self._get_family_members()
        ruview = await self._get_ruview_presence()

        return {
            "status": "ok",
            "members": members,
            "wifi_sensing": ruview,
            "reply_to_user_hebrew": self._format_full_status(members, ruview),
        }

    # ------------------------------------------------------------------
    # Network ping tracking
    # ------------------------------------------------------------------

    async def _ping_host(self, ip: str) -> bool:
        """Ping a host to check if it's on the network."""
        try:
            if platform.system().lower() == "windows":
                cmd = ["ping", "-n", "1", "-w", "1000", ip]
            else:
                cmd = ["ping", "-c", "1", "-W", "1", ip]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=3)
            return proc.returncode == 0
        except Exception:
            return False

    async def _get_ping_members(self) -> list[dict]:
        """Check which tracked phones are on the local network."""
        members = []
        tasks = {ip: self._ping_host(ip) for ip in PING_TRACKED}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for (ip, name), alive in zip(PING_TRACKED.items(), results):
            if isinstance(alive, Exception):
                alive = False
            members.append({
                "name": name,
                "home": alive,
                "state": "home" if alive else "not_home",
                "source": "network_ping",
                "ip": ip,
            })
        return members

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    async def _get_family_members(self) -> list[dict]:
        # 1. HA-tracked members (GPS)
        ha_members = []
        states = await self._ha_get("/api/states")
        if states:
            for s in states:
                eid = s["entity_id"]
                if not eid.startswith("person."):
                    continue

                attrs = s.get("attributes", {})
                name = attrs.get("friendly_name", eid.split(".")[1])

                member = {
                    "name": name,
                    "home": s["state"] == "home",
                    "state": s["state"],
                    "source": "gps",
                }

                tracker_id = attrs.get("source", "")
                if tracker_id:
                    device_key = tracker_id.replace("device_tracker.", "")
                    for sensor in states:
                        sid = sensor["entity_id"]
                        if not sid.startswith(f"sensor.{device_key}"):
                            continue
                        val = sensor["state"]
                        if val in ("unavailable", "unknown"):
                            continue
                        if "geocoded_location" in sid:
                            member["address"] = val
                        elif "battery_level" in sid:
                            member["battery"] = val
                        elif "battery_state" in sid:
                            member["charging"] = val == "Charging"

                ha_members.append(member)

        # 2. Ping-tracked members (network)
        ping_members = await self._get_ping_members()

        # 3. Merge — ping members that aren't already in HA
        ha_names = {m["name"] for m in ha_members}
        members = ha_members[:]
        for pm in ping_members:
            if pm["name"] not in ha_names:
                members.append(pm)

        return members

    async def _get_ruview_presence(self) -> dict:
        ruview = self._registry.get("ruview") if self._registry else None
        if not ruview:
            return {"status": "unavailable"}
        try:
            return await ruview.execute("presence", {})
        except Exception:
            return {"status": "unavailable"}

    # ------------------------------------------------------------------
    # Hebrew formatting
    # ------------------------------------------------------------------

    def _format_who_home(self, home, away) -> str:
        lines = []
        if home:
            names = ", ".join(m["name"] for m in home)
            lines.append(f"בבית: {names}")
        else:
            lines.append("אף אחד לא בבית")
        for m in away:
            addr = m.get("address", "")
            loc = f" ({addr})" if addr else ""
            bat = f" | סוללה: {m['battery']}%" if m.get("battery") else ""
            lines.append(f"  {m['name']}: בחוץ{loc}{bat}")
        return "\n".join(lines)

    def _format_where(self, members) -> str:
        lines = []
        for m in members:
            if m["home"]:
                lines.append(f"• {m['name']}: בבית")
            else:
                addr = m.get("address", "")
                loc = f" — {addr}" if addr else ""
                lines.append(f"• {m['name']}: בחוץ{loc}")
            if m.get("battery"):
                charge = " (בטעינה)" if m.get("charging") else ""
                lines.append(f"  סוללה: {m['battery']}%{charge}")
        return "\n".join(lines)

    def _format_full_status(self, members, ruview) -> str:
        lines = [self._format_where(members)]
        if ruview.get("status") == "ok" and ruview.get("presence"):
            lines.append(f"\nWiFi sensing: {ruview.get('total_people', 0)} נוכחים ({ruview.get('motion_level', '?')})")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Environment awareness
    # ------------------------------------------------------------------

    async def get_presence_snapshot(self) -> dict[str, Any]:
        members = await self._get_family_members()
        home = [m for m in members if m["home"]]
        away = [m for m in members if not m["home"]]
        return {
            "status": "ok",
            "home_count": len(home),
            "away_count": len(away),
            "home": [{"name": m["name"]} for m in home],
            "away": [{"name": m["name"], "address": m.get("address")} for m in away],
        }

    def format_presence_for_prompt(self, snap: dict) -> str:
        if snap.get("status") != "ok":
            return ""
        lines = []
        home = snap.get("home", [])
        away = snap.get("away", [])
        if home:
            names = ", ".join(m["name"] for m in home)
            lines.append(f"   בבית: {names}")
        else:
            lines.append("   אף אחד לא בבית")
        for m in away:
            addr = m.get("address", "")
            loc = f" ({addr})" if addr and addr not in ("unavailable", None) else ""
            lines.append(f"   בחוץ: {m['name']}{loc}")
        return "\n".join(lines)
