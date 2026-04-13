"""RuView WiFi sensing skill — presence detection, vital signs, pose estimation.

Connects to a running RuView Rust sensing server and provides:
- Presence detection: who is in which zone (through-wall WiFi CSI sensing)
- Vital signs: heart rate + breathing rate (contactless, via WiFi signals)
- Pose estimation: body keypoints (17 COCO keypoints per person)
- Activity classification: still/moving/present detection
- Smart room automation: auto lights on entry, context-aware decisions

RuView Rust API endpoints (default port 3000):
  GET /health                        — server health + status
  GET /api/v1/sensing/latest         — full sensing frame (presence + keypoints + vitals)
  GET /api/v1/vital-signs            — vital sign estimates (HR/RR)
  GET /api/v1/model/info             — RVF model container info
  WS  ws://localhost:3001/ws/sensing — real-time WebSocket stream
"""

import logging
import time
from typing import Any

import httpx

from config import get_settings
from core.skill_base import BaseSkill

logger = logging.getLogger(__name__)


class RuViewSensorSkill(BaseSkill):
    name = "ruview"
    description = (
        "WiFi human sensing — detect presence in rooms, read vital signs "
        "(heart rate, breathing rate), estimate pose (body keypoints), "
        "detect activity. Smart room automation: auto-lights on entry."
    )

    RISK_MAP = {
        "presence": "low",
        "vitals": "low",
        "pose": "low",
        "room_status": "low",
        "health": "low",
        "auto_lights_check": "medium",
    }

    def __init__(self, registry=None):
        s = get_settings()
        self.base_url = s.ruview_url.rstrip("/")
        self.enabled = s.ruview_enabled
        self.auto_lights = s.ruview_auto_lights
        self.poll_interval = s.ruview_poll_interval_seconds
        self._registry = registry

        # Track zone state for smart decisions
        self._zone_state: dict[str, dict[str, Any]] = {}
        # Timestamps: zone -> last time someone was detected
        self._last_seen: dict[str, float] = {}
        # Debounce: don't toggle lights too fast
        self._last_light_action: dict[str, float] = {}
        self._LIGHT_COOLDOWN = 10.0  # seconds between light actions per zone

        self._api_retry_after: float = 0.0
        self._API_COOLDOWN = 30.0
        self._api_warned: bool = False

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _api_available(self) -> bool:
        return self.enabled and time.monotonic() >= self._api_retry_after

    async def _get(self, path: str) -> dict | list | None:
        if not self._api_available():
            return None
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get(f"{self.base_url}{path}", timeout=10)
                r.raise_for_status()
                self._api_retry_after = 0.0
                return r.json()
        except Exception as e:
            self._api_retry_after = time.monotonic() + self._API_COOLDOWN
            if not self._api_warned:
                self._api_warned = True
                logger.debug("RuView GET %s failed: %s", path, e)
            return None

    # ------------------------------------------------------------------
    # Skill interface
    # ------------------------------------------------------------------

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        if not self.enabled:
            return {"error": "RuView not enabled. Set JARVIS_RUVIEW_ENABLED=true in .env"}
        try:
            return await method(**(params or {}))
        except Exception as e:
            logger.exception("ruview.%s failed", action)
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Actions (do_*)
    # ------------------------------------------------------------------

    async def do_presence(self, zone: str = "") -> dict:
        """Check who is in the house / a specific zone. Uses WiFi CSI sensing through walls."""
        data = await self._get("/api/v1/sensing/latest")
        if data is None:
            return {"error": "Cannot reach RuView API", "hint": f"Is RuView running on {self.base_url}?"}

        classification = data.get("classification", {})
        persons = data.get("persons", [])
        estimated = data.get("estimated_persons", 0)
        presence = classification.get("presence", False)
        motion = classification.get("motion_level", "unknown")

        # Build zone info from persons
        zones = {}
        for p in persons:
            z = p.get("zone", "main")
            if z not in zones:
                zones[z] = {"zone": z, "count": 0, "occupied": False, "persons": []}
            zones[z]["count"] += 1
            zones[z]["occupied"] = True
            zones[z]["persons"].append(p.get("id"))

        # If no persons but presence detected, add a generic zone
        if presence and not zones:
            zones["main"] = {"zone": "main", "count": estimated, "occupied": True, "persons": []}

        zone_list = list(zones.values())
        if zone:
            zone_lower = zone.lower()
            zone_list = [z for z in zone_list if zone_lower in z["zone"].lower()]

        # Update internal state
        for z in zone_list:
            self._zone_state[z["zone"]] = z
            if z["occupied"]:
                self._last_seen[z["zone"]] = time.monotonic()

        return {
            "status": "ok",
            "presence": presence,
            "motion_level": motion,
            "confidence": classification.get("confidence", 0),
            "total_people": estimated,
            "zones": zone_list,
            "reply_to_user_hebrew": self._format_presence_hebrew(presence, estimated, motion, zone_list),
        }

    async def do_vitals(self) -> dict:
        """Get vital signs — heart rate and breathing rate. Contactless WiFi sensing."""
        data = await self._get("/api/v1/vital-signs")
        if data is None:
            return {"error": "Cannot reach RuView API"}

        vs = data.get("vital_signs", {})
        hr = vs.get("heart_rate_bpm")
        br = vs.get("breathing_rate_bpm")
        hr_conf = vs.get("heartbeat_confidence", 0)
        br_conf = vs.get("breathing_confidence", 0)
        quality = vs.get("signal_quality", 0)

        return {
            "status": "ok",
            "heart_rate_bpm": round(hr, 1) if hr else None,
            "breathing_rate_bpm": round(br, 1) if br else None,
            "heartbeat_confidence": round(hr_conf, 2),
            "breathing_confidence": round(br_conf, 2),
            "signal_quality": round(quality, 2),
            "source": data.get("source", "unknown"),
            "reply_to_user_hebrew": self._format_vitals_hebrew(hr, br, hr_conf, br_conf, quality),
        }

    async def do_pose(self, zone: str = "") -> dict:
        """Get current pose estimation — 17 COCO body keypoints per person, from WiFi signals."""
        data = await self._get("/api/v1/sensing/latest")
        if data is None:
            return {"error": "Cannot reach RuView API"}

        persons = data.get("persons", [])
        if zone:
            zone_lower = zone.lower()
            persons = [p for p in persons if zone_lower in (p.get("zone") or "").lower()]

        # Simplify keypoints for readability
        simplified = []
        for p in persons:
            kps = p.get("keypoints", [])
            simplified.append({
                "id": p.get("id"),
                "confidence": round(p.get("confidence", 0), 2),
                "zone": p.get("zone"),
                "bbox": p.get("bbox"),
                "keypoint_count": len(kps),
                "keypoints": [
                    {"name": k["name"], "confidence": round(k.get("confidence", 0), 2)}
                    for k in kps
                ],
            })

        return {
            "status": "ok",
            "person_count": len(simplified),
            "persons": simplified,
            "source": data.get("source", "unknown"),
        }

    async def do_room_status(self, zone: str = "") -> dict:
        """Full room status: presence + vitals + light state. The smart overview."""
        presence = await self.do_presence(zone=zone)
        vitals = await self.do_vitals()
        light_state = await self._get_room_lights(zone)

        return {
            "status": "ok",
            "presence": presence,
            "vitals": vitals,
            "lights": light_state,
            "reply_to_user_hebrew": self._format_room_status_hebrew(presence, vitals, light_state),
        }

    async def do_health(self) -> dict:
        """Check if RuView system is healthy and running."""
        data = await self._get("/health")
        if data is None:
            return {"status": "offline", "error": f"Cannot reach RuView at {self.base_url}"}
        return {
            "status": data.get("status", "unknown"),
            "source": data.get("source", "unknown"),
            "tick": data.get("tick", 0),
            "clients": data.get("clients", 0),
            "reply_to_user_hebrew": (
                f"RuView {'תקין' if data.get('status') == 'ok' else 'לא תקין'} "
                f"(מקור: {data.get('source', '?')}, tick: {data.get('tick', '?')}, "
                f"clients: {data.get('clients', 0)})"
            ),
        }

    async def do_auto_lights_check(self) -> dict:
        """Smart room automation: check presence and adjust lights.

        Logic:
        - Someone detected + lights off -> turn on
        - Someone detected + night (23:00-06:00) -> dim lights
        - No one detected for >5 min + lights on -> turn off (save energy)
        - Shabbat -> skip automation
        """
        if not self.auto_lights:
            return {"status": "disabled", "message": "Auto-lights disabled in config"}

        presence = await self.do_presence()
        if presence.get("error"):
            return presence

        zones = presence.get("zones", [])
        # If no zones but presence detected, treat as single "main" zone
        if not zones and presence.get("presence"):
            zones = [{"zone": "main", "count": presence.get("total_people", 1), "occupied": True}]

        actions_taken = []
        for zone_data in zones:
            zone_name = zone_data.get("zone", "main")
            occupied = zone_data.get("occupied", False)
            count = zone_data.get("count", 0)

            light_state = await self._get_room_lights(zone_name)
            lights_on = light_state.get("any_on", False)

            action = self._decide_light_action(zone_name, occupied, count, lights_on, light_state)
            if action:
                result = await self._execute_light_action(zone_name, action)
                actions_taken.append({
                    "zone": zone_name,
                    "action": action["action"],
                    "reason": action["reason"],
                    "result": result,
                })

        # Also check for empty zones where lights might still be on
        for zone_name, last_seen in self._last_seen.items():
            if zone_name in [z["zone"] for z in zones if z.get("occupied")]:
                continue  # still occupied
            empty_duration = time.monotonic() - last_seen
            if empty_duration > 300:
                light_state = await self._get_room_lights(zone_name)
                if light_state.get("any_on"):
                    action = {"action": "off", "reason": "החדר ריק כבר 5 דקות — מכבה אור לחיסכון"}
                    result = await self._execute_light_action(zone_name, action)
                    actions_taken.append({
                        "zone": zone_name,
                        "action": "off",
                        "reason": action["reason"],
                        "result": result,
                    })

        return {
            "status": "ok",
            "actions": actions_taken,
            "zones_checked": len(zones),
            "reply_to_user_hebrew": self._format_actions_hebrew(actions_taken),
        }

    # ------------------------------------------------------------------
    # Smart light decision engine
    # ------------------------------------------------------------------

    def _decide_light_action(
        self, zone: str, occupied: bool, count: int, lights_on: bool, light_state: dict,
    ) -> dict | None:
        from datetime import datetime

        now = datetime.now()
        hour = now.hour

        # Shabbat — don't automate lights
        weekday = now.weekday()
        is_shabbat = (weekday == 4 and hour >= 16) or (weekday == 5 and hour < 20)
        if is_shabbat:
            return None

        # Cooldown — don't toggle too fast
        last_action_time = self._last_light_action.get(zone, 0)
        if time.monotonic() - last_action_time < self._LIGHT_COOLDOWN:
            return None

        if occupied and not lights_on:
            if 23 <= hour or hour < 6:
                return {"action": "dim", "brightness": 30, "reason": "נכנס מישהו, שעת לילה — אור עמום"}
            return {"action": "on", "reason": "נכנס מישהו לחדר — מדליק אור"}

        if occupied and lights_on:
            if 23 <= hour or hour < 6:
                current_brightness = light_state.get("brightness")
                if current_brightness and current_brightness > 50:
                    return {"action": "dim", "brightness": 30, "reason": "שעת לילה — מעמעם אור"}
            return None

        if not occupied and lights_on:
            last_seen = self._last_seen.get(zone, 0)
            empty_duration = time.monotonic() - last_seen if last_seen else float("inf")
            if empty_duration > 300:
                return {"action": "off", "reason": "החדר ריק כבר 5 דקות — מכבה אור לחיסכון"}

        return None

    async def _execute_light_action(self, zone: str, action: dict) -> dict:
        smart_home = self._registry.get("smart_home") if self._registry else None
        if not smart_home:
            return {"error": "Smart home skill not available"}

        act = action["action"]
        self._last_light_action[zone] = time.monotonic()

        try:
            if act == "on":
                return await smart_home.execute("turn_on", {"device": zone})
            elif act == "off":
                return await smart_home.execute("turn_off", {"device": zone})
            elif act == "dim":
                brightness = action.get("brightness", 30)
                return await smart_home.execute("set_brightness", {"device": zone, "level": brightness})
            else:
                return {"error": f"Unknown light action: {act}"}
        except Exception as e:
            logger.warning("Light action failed for %s: %s", zone, e)
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Smart home helpers
    # ------------------------------------------------------------------

    async def _get_room_lights(self, zone: str) -> dict:
        smart_home = self._registry.get("smart_home") if self._registry else None
        if not smart_home:
            return {"any_on": False, "source": "unavailable"}

        try:
            result = await smart_home.execute("list_devices", {})
            devices = result.get("devices", [])

            zone_lower = zone.lower() if zone else ""
            room_lights = [
                d for d in devices
                if d.get("type") == "light" and (
                    not zone_lower or zone_lower in d.get("name", "").lower()
                    or zone_lower in d.get("entity_id", "").lower()
                )
            ]

            any_on = any(d.get("state") == "on" for d in room_lights)
            brightness_values = [
                d.get("brightness") for d in room_lights
                if d.get("state") == "on" and d.get("brightness") is not None
            ]

            return {
                "any_on": any_on,
                "lights": room_lights,
                "count": len(room_lights),
                "brightness": brightness_values[0] if brightness_values else None,
                "source": "smart_home",
            }
        except Exception as e:
            logger.debug("Failed to get room lights: %s", e)
            return {"any_on": False, "source": "error"}

    # ------------------------------------------------------------------
    # Hebrew formatting
    # ------------------------------------------------------------------

    def _format_presence_hebrew(self, presence, count, motion, zones) -> str:
        if not presence:
            return "לא מזוהה נוכחות כרגע."
        motion_he = {
            "present_still": "נוכח, ללא תנועה",
            "present_moving": "נוכח, בתנועה",
            "absent": "לא נוכח",
        }.get(motion, motion)
        lines = [f"זוהו {count} {'אנשים' if count > 1 else 'אדם'} ({motion_he})"]
        for z in zones:
            lines.append(f"• {z['zone']}: {z['count']} נוכחים")
        return "\n".join(lines)

    def _format_vitals_hebrew(self, hr, br, hr_conf, br_conf, quality) -> str:
        lines = []
        if hr:
            lines.append(f"דופק: {hr:.0f} BPM (ביטחון: {hr_conf:.0%})")
        if br:
            lines.append(f"נשימה: {br:.1f} לדקה (ביטחון: {br_conf:.0%})")
        lines.append(f"איכות אות: {quality:.0%}")
        return "\n".join(lines) if lines else "אין נתוני סימנים חיוניים."

    def _format_room_status_hebrew(self, presence, vitals, lights) -> str:
        lines = []
        if presence.get("presence"):
            lines.append(f"📡 נוכחות: {presence.get('total_people', 0)} אנשים ({presence.get('motion_level', '?')})")
        else:
            lines.append("📡 נוכחות: לא מזוהה")

        hr = vitals.get("heart_rate_bpm")
        br = vitals.get("breathing_rate_bpm")
        if hr or br:
            parts = []
            if hr:
                parts.append(f"דופק {hr}")
            if br:
                parts.append(f"נשימה {br:.1f}")
            lines.append(f"💓 {', '.join(parts)}")

        if lights.get("any_on"):
            lines.append(f"💡 אורות: דולקים ({lights.get('count', 0)} נורות)")
        else:
            lines.append("💡 אורות: כבויים")

        return "\n".join(lines)

    def _format_actions_hebrew(self, actions: list[dict]) -> str:
        if not actions:
            return "בדקתי את כל החדרים — הכל מסודר, אין צורך בשינוי."
        lines = []
        for a in actions:
            lines.append(f"• {a['zone']}: {a['reason']}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Environment awareness integration
    # ------------------------------------------------------------------

    async def get_sensing_snapshot(self) -> dict[str, Any]:
        """Return a snapshot for EnvironmentAwareness to include in the system prompt."""
        if not self._api_available():
            return {"status": "unavailable"}

        data = await self._get("/api/v1/sensing/latest")
        if data is None:
            return {"status": "offline"}

        classification = data.get("classification", {})
        vs = data.get("vital_signs", {})

        return {
            "status": "ok",
            "presence": classification.get("presence", False),
            "total_people": data.get("estimated_persons", 0),
            "motion_level": classification.get("motion_level", "unknown"),
            "confidence": classification.get("confidence", 0),
            "heart_rate_bpm": vs.get("heart_rate_bpm"),
            "breathing_rate_bpm": vs.get("breathing_rate_bpm"),
        }

    def format_sensing_for_prompt(self, snap: dict) -> str:
        """Format sensing data for the system prompt (Hebrew)."""
        if snap.get("status") != "ok":
            return "📡 חישת WiFi: לא זמין"

        lines = ["📡 חישת WiFi (RuView):"]
        if snap.get("presence"):
            motion_he = {
                "present_still": "ללא תנועה",
                "present_moving": "בתנועה",
            }.get(snap.get("motion_level", ""), "")
            lines.append(f"   נוכחות: {snap.get('total_people', 0)} אנשים {motion_he}".rstrip())
            hr = snap.get("heart_rate_bpm")
            br = snap.get("breathing_rate_bpm")
            if hr:
                lines.append(f"   דופק: {hr:.0f} BPM")
            if br:
                lines.append(f"   נשימה: {br:.1f} לדקה")
        else:
            lines.append("   לא מזוהה נוכחות")

        return "\n".join(lines)
