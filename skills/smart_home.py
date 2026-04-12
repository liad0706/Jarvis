"""Smart home skill — control lights and devices via Home Assistant.

Fallback: direct Yeelight/Kasa LAN control if HA is not available.
"""

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx

from config import get_settings
from core.skill_base import BaseSkill

logger = logging.getLogger(__name__)

CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "smart_devices.json"


def _safe_int(val, default: int, lo: int, hi: int) -> int:
    try:
        if val is None or val == "":
            return default
        n = int(float(str(val).strip()))
        return max(lo, min(hi, n))
    except (ValueError, TypeError):
        return default


class SmartHomeSkill(BaseSkill):
    name = "smart_home"
    description = (
        "Control smart lights via Home Assistant (turn on/off one or ALL lights, brightness, color, list devices). "
        "Use turn_off_all_lights for 'כבה הכל' / all lights off."
    )

    RISK_MAP = {
        "discover_devices": "low",
        "list_devices": "low",
        "turn_on": "medium",
        "turn_off": "medium",
        "turn_off_all_lights": "medium",
        "turn_on_all_lights": "medium",
        "off_then_on": "medium",
        "on_off_cycles": "medium",
        "set_brightness": "medium",
        "set_color": "medium",
        "toggle": "medium",
    }

    def __init__(self):
        s = get_settings()
        self.ha_url = s.ha_url.rstrip("/")
        self.ha_token = s.ha_token
        self._ha_retry_after = 0.0
        self._ha_retry_cooldown_seconds = 60.0
        self._headers = {
            "Authorization": f"Bearer {self.ha_token}",
            "Content-Type": "application/json",
        }

    def _ha_available(self) -> bool:
        return bool(self.ha_token)

    def _ha_retry_allowed(self) -> bool:
        return time.monotonic() >= self._ha_retry_after

    def _mark_ha_unavailable(self, method: str, path: str, error: Exception) -> None:
        self._ha_retry_after = time.monotonic() + self._ha_retry_cooldown_seconds
        logger.warning(
            "HA %s %s failed: %s (suppressing retries for %.0fs)",
            method,
            path,
            error,
            self._ha_retry_cooldown_seconds,
        )

    async def _ha_get(self, path: str) -> dict | list | None:
        if not self._ha_retry_allowed():
            return None
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get(f"{self.ha_url}{path}", headers=self._headers, timeout=10)
                r.raise_for_status()
                self._ha_retry_after = 0.0
                return r.json()
        except Exception as e:
            self._mark_ha_unavailable("GET", path, e)
            return None

    async def _ha_post(self, path: str, data: dict | None = None) -> dict | list | None:
        if not self._ha_retry_allowed():
            return None
        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(f"{self.ha_url}{path}", headers=self._headers, json=data or {}, timeout=10)
                r.raise_for_status()
                self._ha_retry_after = 0.0
                return r.json()
        except Exception as e:
            self._mark_ha_unavailable("POST", path, e)
            return None

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        try:
            return await method(**(params or {}))
        except Exception as e:
            logger.exception("smart_home.%s failed", action)
            return {"error": str(e)}

    async def do_discover_devices(self) -> dict:
        """Scan for smart home devices. Uses Home Assistant if configured, otherwise scans LAN directly."""
        if self._ha_available():
            result = await self._ha_discover()
            if result.get("found", 0) > 0:
                return result
            lan_result = await self._lan_discover()
            if lan_result.get("found", 0) > 0:
                lan_result["fallback_reason"] = result.get("error", "home_assistant_unavailable")
                return lan_result
            return result
        return await self._lan_discover()

    async def _ha_discover(self) -> dict:
        states = await self._ha_get("/api/states")
        if states is None:
            return {"error": "Cannot connect to Home Assistant. Check URL and token."}

        devices = []
        for entity in states:
            eid = entity.get("entity_id", "")
            if not any(eid.startswith(d) for d in ("light.", "switch.", "fan.", "cover.")):
                continue
            attr = entity.get("attributes", {})
            devices.append({
                "entity_id": eid,
                "name": attr.get("friendly_name", eid),
                "state": entity.get("state", "unknown"),
                "type": eid.split(".")[0],
                "brightness": attr.get("brightness"),
                "color": attr.get("rgb_color"),
            })

        self._save_cache(devices)
        return {
            "status": "ok",
            "source": "home_assistant",
            "found": len(devices),
            "devices": devices,
        }

    async def _lan_discover(self) -> dict:
        devices = []
        try:
            from yeelight import discover_bulbs
            found = await asyncio.get_event_loop().run_in_executor(None, discover_bulbs)
            for b in found:
                cap = b.get("capabilities", {})
                devices.append({
                    "entity_id": f"yeelight_{b.get('ip', '')}",
                    "name": cap.get("name", "") or f"Yeelight {b.get('ip', '')}",
                    "state": cap.get("power", "unknown"),
                    "type": "light",
                    "ip": b.get("ip", ""),
                    "source": "lan",
                })
        except ImportError:
            logger.debug("yeelight not installed — skipping LAN scan")
        except Exception as e:
            logger.warning("Yeelight LAN scan failed: %s", e)

        try:
            from kasa import Discover
            found = await Discover.discover(timeout=5)
            for ip, dev in found.items():
                await dev.update()
                devices.append({
                    "entity_id": f"kasa_{ip}",
                    "name": dev.alias or f"Kasa {ip}",
                    "state": "on" if dev.is_on else "off",
                    "type": "light",
                    "ip": ip,
                    "source": "lan",
                })
        except ImportError:
            logger.debug("kasa not installed — skipping LAN scan")
        except Exception as e:
            logger.warning("Kasa LAN scan failed: %s", e)

        if devices:
            self._save_cache(devices)
        return {"status": "ok", "source": "lan", "found": len(devices), "devices": devices}

    async def do_list_devices(self) -> dict:
        """List all known smart home devices (lights, switches, etc.)."""
        if self._ha_available():
            result = await self._ha_discover()
            if result.get("found", 0) > 0:
                return result
            lan_result = await self._lan_discover()
            if lan_result.get("found", 0) > 0:
                lan_result["fallback_reason"] = result.get("error", "home_assistant_unavailable")
                return lan_result
        cached = self._load_cache()
        if cached:
            return {"status": "ok", "count": len(cached), "devices": cached}
        return {"status": "empty", "message": "No devices found. Run discover_devices first or configure Home Assistant."}

    async def do_turn_on(self, device: str = "") -> dict:
        """Turn ON a light or device. Use device name or entity_id. Leave empty for first light found."""
        return await self._control("turn_on", device)

    async def do_turn_off(self, device: str = "") -> dict:
        """Turn OFF a light or device. Use device name or entity_id. Leave empty for first light found."""
        return await self._control("turn_off", device)

    async def do_turn_off_all_lights(self) -> dict:
        """Turn OFF every light in Home Assistant. Use for 'כבה הכל', 'כבה את כל האורות', 'turn off all lights'."""
        if not self._ha_available():
            return {"error": "Home Assistant not configured (need JARVIS_HA_TOKEN)"}

        states = await self._ha_get("/api/states")
        if states is None:
            return {"error": "Cannot reach Home Assistant"}

        lights = [
            e["entity_id"]
            for e in states
            if str(e.get("entity_id", "")).startswith("light.")
        ]
        if not lights:
            return {"status": "ok", "count": 0, "message": "No light.* entities found"}

        ok, failed = 0, []
        for eid in lights:
            r = await self._ha_post("/api/services/light/turn_off", {"entity_id": eid})
            if r is not None:
                ok += 1
            else:
                failed.append(eid)

        return {
            "status": "ok" if not failed else "partial",
            "turned_off": ok,
            "total": len(lights),
            "failed": failed,
            "reply_to_user_hebrew": (
                f"כיביתי {ok} מתוך {len(lights)} אורות."
                + (f" נכשלו: {', '.join(failed)}" if failed else "")
            ),
        }

    async def do_turn_on_all_lights(self) -> dict:
        """Turn ON every light in Home Assistant (default state; no color/brightness)."""
        if not self._ha_available():
            return {"error": "Home Assistant not configured (need JARVIS_HA_TOKEN)"}

        states = await self._ha_get("/api/states")
        if states is None:
            return {"error": "Cannot reach Home Assistant"}

        lights = [
            e["entity_id"]
            for e in states
            if str(e.get("entity_id", "")).startswith("light.")
        ]
        if not lights:
            return {"status": "ok", "count": 0, "message": "No light.* entities found"}

        ok, failed = 0, []
        for eid in lights:
            r = await self._ha_post("/api/services/light/turn_on", {"entity_id": eid})
            if r is not None:
                ok += 1
            else:
                failed.append(eid)

        return {
            "status": "ok" if not failed else "partial",
            "turned_on": ok,
            "total": len(lights),
            "failed": failed,
            "reply_to_user_hebrew": (
                f"הדלקתי {ok} מתוך {len(lights)} אורות."
                + (f" נכשלו: {', '.join(failed)}" if failed else "")
            ),
        }

    async def do_toggle(self, device: str = "") -> dict:
        """Toggle a light or device on/off."""
        return await self._control("toggle", device)

    async def do_off_then_on(self, pause_seconds: int = 5, device: str = "") -> dict:
        """Turn light OFF, wait pause_seconds (default 5) so the user can SEE it off, then turn ON. Use for 'כבה ואז הדלק' / off then on — do NOT use two separate turn_off + turn_on in one reply without this tool."""
        if self._ha_available():
            await self._ha_discover()
        pause = _safe_int(pause_seconds, 5, 1, 60)
        off_r = await self.do_turn_off(device=device)
        if off_r.get("error"):
            return off_r
        logger.info("off_then_on: waiting %s s between off and on", pause)
        await asyncio.sleep(pause)
        on_r = await self.do_turn_on(device=device)
        if on_r.get("error"):
            return {
                "status": "partial",
                "off": "ok",
                "pause_seconds": pause,
                "error": on_r.get("error"),
            }
        return {
            "status": "ok",
            "device": off_r.get("device"),
            "pause_seconds": pause,
            "message": f"כביתי, המתנתי {pause} שניות, והדלקתי שוב.",
        }

    async def do_on_off_cycles(self, cycles: int = 3, pause_seconds: int = 3, device: str = "") -> dict:
        """For 'תדליק ותכבה N פעמים' / blink N times: each cycle is ON, wait pause_seconds, OFF, wait pause_seconds (default 3s so you can SEE each state). ONE tool call — never spam many turn_on/turn_off in one LLM turn."""
        if self._ha_available():
            await self._ha_discover()
        cycles = _safe_int(cycles, 3, 1, 20)
        pause = _safe_int(pause_seconds, 3, 1, 30)

        entity = await self._resolve_entity(device, "light")
        if not entity:
            discover = await self.do_discover_devices()
            if discover.get("found", 0) == 0:
                return {"error": "No devices found."}
            entity = await self._resolve_entity(device, "light")
            if not entity:
                return {"error": "Device not found. Run list_devices."}

        for i in range(cycles):
            r_on = await self._apply_entity_action(entity, "turn_on")
            if r_on.get("error"):
                return {"error": f"מחזור {i + 1} הדלקה: {r_on['error']}", "completed_cycles": i}
            await asyncio.sleep(pause)
            r_off = await self._apply_entity_action(entity, "turn_off")
            if r_off.get("error"):
                return {"error": f"מחזור {i + 1} כיבוי: {r_off['error']}", "completed_cycles": i}
            await asyncio.sleep(pause)

        dev = entity.get("name", entity.get("entity_id"))
        return {
            "status": "ok",
            "cycles": cycles,
            "pause_seconds": pause,
            "device": dev,
            "final_state": "off",
            "message": (
                f"סיימתי בהצלחה: {cycles} מחזורי הדלקה וכיבוי על {dev}. "
                f"בכל שלב המתנתי כ-{pause} שניות. המצב עכשיו: כבוי."
            ),
            "reply_to_user_hebrew": (
                f"סיימתי — האור הודלק וכובה {cycles} פעמים (עם המתנה של כ-{pause} שניות בין מצבים). "
                f"עכשיו הוא כבוי."
            ),
        }

    async def do_set_brightness(self, level: int = 100, device: str = "") -> dict:
        """Set brightness of a light (0-100). Example: level=50 for half brightness."""
        entity = await self._resolve_entity(device, "light")
        if not entity:
            return {"error": f"Device not found: '{device}'. Run discover_devices first."}

        eid = entity["entity_id"]
        if self._ha_available() and not entity.get("source") == "lan":
            brightness_255 = max(1, min(255, int(int(level) * 2.55)))
            result = await self._ha_post(f"/api/services/light/turn_on", {
                "entity_id": eid,
                "brightness": brightness_255,
            })
            if result is not None:
                return {"status": "ok", "device": entity.get("name", eid), "brightness": level}
            return {"error": f"Failed to set brightness for {eid}"}
        return {"error": "Brightness control requires Home Assistant"}

    async def do_set_color(self, r: int = 255, g: int = 255, b: int = 255, device: str = "") -> dict:
        """Set color of a light using RGB (0-255 each). Example: r=255, g=0, b=0 for red."""
        entity = await self._resolve_entity(device, "light")
        if not entity:
            return {"error": f"Device not found: '{device}'. Run discover_devices first."}

        eid = entity["entity_id"]
        if self._ha_available() and not entity.get("source") == "lan":
            result = await self._ha_post(f"/api/services/light/turn_on", {
                "entity_id": eid,
                "rgb_color": [int(r), int(g), int(b)],
            })
            if result is not None:
                return {"status": "ok", "device": entity.get("name", eid), "color": [r, g, b]}
            return {"error": f"Failed to set color for {eid}"}
        return {"error": "Color control requires Home Assistant"}

    async def _control(self, action: str, device: str) -> dict:
        entity = await self._resolve_entity(device, "light")
        if not entity:
            discover = await self.do_discover_devices()
            if discover.get("found", 0) == 0:
                return {"error": "No devices found on the network or in Home Assistant."}
            entity = await self._resolve_entity(device, "light")
            if not entity:
                names = [d.get("name", d.get("entity_id", "?")) for d in discover.get("devices", [])]
                return {"error": f"Device '{device}' not found. Available: {', '.join(names)}"}

        return await self._apply_entity_action(entity, action)

    async def _apply_entity_action(self, entity: dict, action: str) -> dict:
        eid = entity["entity_id"]
        name = entity.get("name", eid)

        if self._ha_available() and entity.get("source") != "lan":
            domain = eid.split(".")[0]
            result = await self._ha_post(f"/api/services/{domain}/{action}", {"entity_id": eid})
            if result is not None:
                return {"status": "ok", "device": name, "action": action}
            return {"error": f"HA command failed for {eid}"}

        ip = entity.get("ip")
        if ip and "yeelight" in eid:
            return await self._yeelight_control(ip, name, action)
        return {"error": f"Cannot control {name} without Home Assistant"}

    async def _yeelight_control(self, ip: str, name: str, action: str) -> dict:
        from yeelight import Bulb
        bulb = Bulb(ip)
        try:
            act = {"turn_on": bulb.turn_on, "turn_off": bulb.turn_off, "toggle": bulb.toggle}
            fn = act.get(action)
            if fn:
                await asyncio.get_event_loop().run_in_executor(None, fn)
                return {"status": "ok", "device": name, "action": action}
            return {"error": f"Unknown action: {action}"}
        except Exception as e:
            return {"error": f"Yeelight {name} ({ip}): {e}"}

    async def _resolve_entity(self, device: str, preferred_type: str = "") -> dict | None:
        cached = self._load_cache()
        if not cached and self._ha_available():
            result = await self._ha_discover()
            cached = result.get("devices", [])

        if not cached:
            return None

        if not device:
            for d in cached:
                if preferred_type and d.get("type", "") == preferred_type:
                    return d
            return cached[0] if cached else None

        device_lower = device.lower().strip()
        for d in cached:
            eid = d.get("entity_id", "").lower()
            name = d.get("name", "").lower()
            if device_lower in name or device_lower in eid or device_lower == eid:
                return d
        return None

    def _load_cache(self) -> list[dict]:
        if CACHE_FILE.exists():
            try:
                return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return []

    def _save_cache(self, devices: list[dict]):
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(devices, indent=2, ensure_ascii=False), encoding="utf-8")
