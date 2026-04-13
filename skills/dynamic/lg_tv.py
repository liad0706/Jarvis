"""LG webOS TV control skill — direct WebSocket (wss://TV:3001).

Works with webOS 10.x (2024+) models.
No extra dependencies — uses stdlib ssl + websockets (already installed).

Settings: LG_TV_IP in .env, client key auto-saved to data/lg_tv_key.json.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import ssl
from pathlib import Path
from typing import Any

from core.skill_base import BaseSkill

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
KEY_FILE = DATA_DIR / "lg_tv_key.json"

# Full manifest from Home Assistant webostv integration
_HANDSHAKE_PAYLOAD = {
    "forcePairing": False,
    "pairingType": "PROMPT",
    "manifest": {
        "manifestVersion": 1,
        "appVersion": "1.1",
        "signed": {
            "created": "20140509",
            "appId": "com.lge.test",
            "vendorId": "com.lge",
            "localizedAppNames": {"": "Jarvis", "ko-KR": "Jarvis"},
            "localizedVendorNames": {"": "LG Electronics"},
            "permissions": [
                "TEST_SECURE", "CONTROL_INPUT_TEXT", "CONTROL_MOUSE_AND_KEYBOARD",
                "READ_INSTALLED_APPS", "READ_LGE_SDX", "READ_NOTIFICATIONS",
                "SEARCH", "WRITE_SETTINGS", "WRITE_NOTIFICATION_ALERT",
                "CONTROL_POWER", "READ_CURRENT_CHANNEL", "READ_RUNNING_APPS",
                "READ_UPDATE_INFO", "UPDATE_FROM_REMOTE_APP", "READ_LGE_TV_INPUT_EVENTS",
                "READ_TV_CURRENT_TIME", "CONTROL_AUDIO", "CONTROL_DISPLAY",
                "CONTROL_INPUT_JOYSTICK", "CONTROL_INPUT_MEDIA_RECORDING",
                "CONTROL_INPUT_MEDIA_PLAYBACK", "CONTROL_INPUT_TV",
                "LAUNCH", "LAUNCH_WEBAPP", "APP_TO_APP", "CLOSE",
                "TEST_OPEN", "TEST_PROTECTED",
                "CONTROL_TV_SCREEN", "CONTROL_TV_STANBY",
                "CONTROL_FAVORITE_GROUP", "CONTROL_USER_INFO",
                "CHECK_BLUETOOTH_DEVICE", "CONTROL_BLUETOOTH",
                "CONTROL_TIMER_INFO", "STB_INTERNAL_CONNECTION",
                "CONTROL_RECORDING", "READ_RECORDING_STATE",
                "WRITE_NOTIFICATION_TOAST", "READ_STORAGE_DEVICE_LIST",
                "READ_SECURE_STORAGE", "WRITE_SECURE_STORAGE",
                "READ_COUNTRY_INFO", "READ_SETTINGS",
                "CONTROL_TV_POWER", "READ_APP_STATUS",
                "CONTROL_NETWORK_WIFI",
            ],
            "serial": "serial",
        },
        "permissions": [
            "LAUNCH", "LAUNCH_WEBAPP", "APP_TO_APP", "CONTROL_AUDIO",
            "CONTROL_DISPLAY", "CONTROL_INPUT_JOYSTICK", "CONTROL_INPUT_MEDIA_PLAYBACK",
            "CONTROL_INPUT_MEDIA_RECORDING", "CONTROL_INPUT_TEXT",
            "CONTROL_INPUT_TV", "CONTROL_MOUSE_AND_KEYBOARD", "CONTROL_POWER",
            "CONTROL_TV_SCREEN", "READ_APP_STATUS", "READ_CURRENT_CHANNEL",
            "READ_INPUT_DEVICE_LIST", "READ_INSTALLED_APPS", "READ_NETWORK_STATE",
            "READ_RUNNING_APPS", "READ_TV_CHANNEL_LIST", "WRITE_NOTIFICATION_TOAST",
            "READ_POWER_STATE", "READ_COUNTRY_INFO", "READ_SETTINGS",
            "CONTROL_TV_POWER", "READ_TV_CURRENT_TIME",
            "READ_STORAGE_DEVICE_LIST", "CONTROL_NETWORK_WIFI",
        ],
        "signatures": [{
            "signatureVersion": 1,
            "signature": (
                "eyJhbGdvcml0aG0iOiJSU0EtU0hBMjU2Iiwia2V5SWQiOiJ0ZXN0LXNpZ25pbm"
                "ctY2VydCIsInNpZ25hdHVyZVZlcnNpb24iOjF9.hrVRgjCwXVvE2OOSpDZ58hR+"
                "59aFNwYDyjQgKk3auukd7pcegmE2CzPCa0bJ0ZsRAcKkCTJrWo5iDzNhMBWRya"
                "MOv5zWSrthlf7G128qvIlpMT0YNY+n/FaOHE73uLrS/g7swl3/qH/BGFG2Hu4"
                "RlL48eb3lLKqTt2xKHdCs6Cd4RMfJPYnzgvI4BNrFUKsjkcu+WD4OO2A27Pq1n"
                "50cMchmcaXadJhGrOqH5YmHdOCj5NSHzJYrsW0HPlpuAx/ECMeIZYDh6RMqaFM"
                "2DXzdKX9NmmyqzJ3o/0lkk/N97gfVRLW5hA29yeAwaCViZNCP8iC9aO0q9fQoj"
                "oa7NQnAtw=="
            ),
        }],
    },
}


def _load_key() -> str | None:
    try:
        if KEY_FILE.exists():
            return json.loads(KEY_FILE.read_text("utf-8")).get("client_key")
    except Exception:
        pass
    return None


def _save_key(key: str):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_text(json.dumps({"client_key": key}), "utf-8")
    logger.info("LG TV key saved")


class _LgTvConnection:
    """Low-level websocket connection to an LG webOS TV."""

    def __init__(self, ip: str, client_key: str | None = None):
        self.ip = ip
        self.client_key = client_key
        self._ws = None
        self._req_id = 0

    async def connect(self, timeout: float = 10):
        import websockets

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        self._ws = await websockets.connect(
            f"wss://{self.ip}:3001", ssl=ctx, open_timeout=timeout,
        )

        # Send registration handshake
        payload = dict(_HANDSHAKE_PAYLOAD)
        if self.client_key:
            payload["client-key"] = self.client_key

        msg = {"type": "register", "id": "register_0", "payload": payload}
        await self._ws.send(json.dumps(msg))

        # Read responses until registered
        for _ in range(5):
            raw = await asyncio.wait_for(self._ws.recv(), timeout=15)
            data = json.loads(raw)
            p = data.get("payload", {})
            if "client-key" in p:
                self.client_key = p["client-key"]
                _save_key(self.client_key)
            if data.get("type") == "registered":
                logger.info("LG TV registered at %s", self.ip)
                return
        raise RuntimeError("TV did not confirm registration")

    async def request(self, uri: str, payload: dict | None = None, _retried: bool = False) -> dict:
        if not self._ws:
            if _retried:
                raise RuntimeError("Not connected")
            await self.connect()

        self._req_id += 1
        msg: dict[str, Any] = {
            "type": "request",
            "id": f"req_{self._req_id}",
            "uri": uri,
        }
        if payload:
            msg["payload"] = payload
        try:
            await self._ws.send(json.dumps(msg))
            raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
        except Exception:
            if _retried:
                raise
            # Reconnect and retry once
            self._ws = None
            await self.connect()
            return await self.request(uri, payload, _retried=True)

        data = json.loads(raw)
        if data.get("type") == "error":
            raise RuntimeError(data.get("error", "unknown error"))
        return data.get("payload", {})

    async def disconnect(self):
        if self._ws:
            await self._ws.close()
            self._ws = None


class LgTvSkill(BaseSkill):
    name = "lg_tv"
    description = (
        "Control LG webOS TV — volume up/down/set/mute, power off, "
        "open apps (Netflix/YouTube/etc), switch HDMI input, get status. "
        "Volume range 0-100. Use pair first if not yet paired."
    )

    RISK_MAP = {
        "pair": "medium",
        "status": "low",
        "volume_up": "low",
        "volume_down": "low",
        "set_volume": "low",
        "get_volume": "low",
        "mute": "low",
        "unmute": "low",
        "power_off": "medium",
        "open_app": "low",
        "list_apps": "low",
        "set_input": "low",
        "toast": "low",
    }

    def __init__(self):
        self._conn: _LgTvConnection | None = None

    def _get_ip(self) -> str:
        ip = os.getenv("LG_TV_IP", "")
        if not ip:
            raise RuntimeError("LG_TV_IP לא מוגדר ב-.env")
        return ip

    async def _ensure(self) -> _LgTvConnection:
        if self._conn and self._conn._ws:
            try:
                # Quick ping check
                await self._conn._ws.ping()
                return self._conn
            except Exception:
                self._conn = None

        ip = self._get_ip()
        key = _load_key()
        conn = _LgTvConnection(ip, key)
        await conn.connect()
        self._conn = conn
        return conn

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        try:
            return await method(**(params or {}))
        except Exception as e:
            self._conn = None  # reset on error
            logger.error("LG TV %s failed: %s", action, e)
            return {"error": str(e), "reply_to_user_hebrew": f"שגיאה בטלוויזיה: {e}"}

    # ------------------------------------------------------------------
    # Pairing
    # ------------------------------------------------------------------

    async def do_pair(self, ip: str = "") -> dict:
        """Pair with the TV. Shows a prompt on the TV screen — press OK."""
        target = ip or os.getenv("LG_TV_IP", "")
        if not target:
            return {"error": "חסר IP — תגדיר LG_TV_IP ב-.env"}

        os.environ["LG_TV_IP"] = target
        conn = _LgTvConnection(target, None)
        # forcePairing for fresh pair
        await conn.connect(timeout=30)
        self._conn = conn
        return {
            "status": "paired",
            "ip": target,
            "client_key": conn.client_key,
            "reply_to_user_hebrew": f"מחובר לטלוויזיה LG ({target})! ✅",
        }

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def do_status(self) -> dict:
        """Get TV status — volume, current app."""
        conn = await self._ensure()
        vol_data = await conn.request("ssap://audio/getVolume")
        app_data = await conn.request(
            "ssap://com.webos.applicationManager/getForegroundAppInfo"
        )
        volume = vol_data.get("volume", vol_data.get("volumeStatus", {}).get("volume", "?"))
        muted = vol_data.get("mute", False)
        app = app_data.get("appId", "?")
        mute_txt = " (מושתק)" if muted else ""
        return {
            "volume": volume,
            "muted": muted,
            "current_app": app,
            "reply_to_user_hebrew": f"טלוויזיה: ווליום {volume}{mute_txt}, אפליקציה: {app}",
        }

    async def do_get_volume(self) -> dict:
        """Get current volume level."""
        conn = await self._ensure()
        data = await conn.request("ssap://audio/getVolume")
        vol = data.get("volume", data.get("volumeStatus", {}).get("volume", "?"))
        return {"volume": vol, "reply_to_user_hebrew": f"ווליום: {vol}"}

    # ------------------------------------------------------------------
    # Volume
    # ------------------------------------------------------------------

    async def do_volume_up(self, steps: int = 1) -> dict:
        """Raise volume. steps = how many clicks (default 1, max 20)."""
        try:
            steps = int(steps)
        except (TypeError, ValueError):
            steps = 1
        steps = max(1, min(steps, 20))
        conn = await self._ensure()
        for _ in range(steps):
            await conn.request("ssap://audio/volumeUp")
        data = await conn.request("ssap://audio/getVolume")
        vol = data.get("volume", "?")
        return {"volume": vol, "reply_to_user_hebrew": f"הגברתי ← ווליום {vol}"}

    async def do_volume_down(self, steps: int = 1) -> dict:
        """Lower volume. steps = how many clicks (default 1, max 20)."""
        try:
            steps = int(steps)
        except (TypeError, ValueError):
            steps = 1
        steps = max(1, min(steps, 20))
        conn = await self._ensure()
        for _ in range(steps):
            await conn.request("ssap://audio/volumeDown")
        data = await conn.request("ssap://audio/getVolume")
        vol = data.get("volume", "?")
        return {"volume": vol, "reply_to_user_hebrew": f"הנמכתי ← ווליום {vol}"}

    async def do_set_volume(self, level: int = 15) -> dict:
        """Set volume to exact level (0-100)."""
        try:
            level = int(level)
        except (TypeError, ValueError):
            return {"error": "ווליום חייב להיות מספר בין 0-100"}
        if not 0 <= level <= 100:
            return {"error": "ווליום חייב להיות 0-100"}
        conn = await self._ensure()
        await conn.request("ssap://audio/setVolume", {"volume": level})
        return {"volume": level, "reply_to_user_hebrew": f"ווליום → {level}"}

    async def do_gradual_volume(self, start: int = 5, end: int = 20, interval: int = 60) -> dict:
        """Gradually raise volume from *start* to *end*, increasing by 1 every *interval* seconds.

        Good for gentle wake-up alarms.  Example: start=5, end=20, interval=60
        means volume goes 5→6→7→…→20 over 15 minutes.
        """
        try:
            start = int(start)
            end = int(end)
            interval = int(interval)
        except (TypeError, ValueError):
            return {"error": "start, end, interval must be numbers"}
        start = max(0, min(start, 100))
        end = max(0, min(end, 100))
        interval = max(10, min(interval, 300))  # 10s-5min

        if start >= end:
            return {"error": f"start ({start}) must be less than end ({end})"}

        # Set initial volume immediately
        try:
            conn = await self._ensure()
            await conn.request("ssap://audio/setVolume", {"volume": start})
        except Exception as e:
            return {"error": f"Failed to set initial volume: {e}"}

        # Background ramp
        async def _ramp():
            for vol in range(start + 1, end + 1):
                await asyncio.sleep(interval)
                try:
                    c = await self._ensure()
                    await c.request("ssap://audio/setVolume", {"volume": vol})
                    logger.info("Gradual volume: %d/%d", vol, end)
                except Exception as e:
                    logger.warning("Gradual volume failed at %d: %s", vol, e)
                    break

        asyncio.create_task(_ramp())
        total_min = ((end - start) * interval) // 60
        return {
            "reply_to_user_hebrew": f"ווליום עולה הדרגתית: {start} -> {end} במשך {total_min} דקות",
            "start": start, "end": end, "interval_sec": interval,
        }

    async def do_mute(self) -> dict:
        """Mute the TV."""
        conn = await self._ensure()
        await conn.request("ssap://audio/setMute", {"mute": True})
        return {"muted": True, "reply_to_user_hebrew": "הטלוויזיה מושתקת 🔇"}

    async def do_unmute(self) -> dict:
        """Unmute the TV."""
        conn = await self._ensure()
        await conn.request("ssap://audio/setMute", {"mute": False})
        return {"muted": False, "reply_to_user_hebrew": "ביטלתי השתקה 🔊"}

    # ------------------------------------------------------------------
    # Power
    # ------------------------------------------------------------------

    async def do_power_off(self) -> dict:
        """Turn off the TV."""
        conn = await self._ensure()
        await conn.request("ssap://system/turnOff")
        self._conn = None
        return {"reply_to_user_hebrew": "כיביתי את הטלוויזיה"}

    # ------------------------------------------------------------------
    # Apps
    # ------------------------------------------------------------------

    async def do_list_apps(self) -> dict:
        """List installed apps on the TV."""
        conn = await self._ensure()
        data = await conn.request("ssap://com.webos.applicationManager/listApps")
        apps = data.get("apps", [])
        names = sorted(a.get("title", a.get("id", "?")) for a in apps)[:25]
        return {
            "apps": names,
            "reply_to_user_hebrew": "אפליקציות:\n" + "\n".join(f"• {n}" for n in names),
        }

    async def do_open_app(self, app_name: str = "") -> dict:
        """Open an app by name (Netflix, YouTube, Disney+, Spotify, etc.)."""
        if not app_name:
            return {"error": "חסר שם אפליקציה"}
        conn = await self._ensure()
        data = await conn.request("ssap://com.webos.applicationManager/listApps")
        apps = data.get("apps", [])

        app_lower = app_name.lower()
        match = None
        for a in apps:
            title = a.get("title", "")
            if app_lower == title.lower():
                match = a
                break
        if not match:
            for a in apps:
                if app_lower in a.get("title", "").lower() or app_lower in a.get("id", "").lower():
                    match = a
                    break
        if not match:
            return {"error": f"לא מצאתי '{app_name}'"}

        await conn.request("ssap://system.launcher/launch", {"id": match["id"]})
        return {"reply_to_user_hebrew": f"פתחתי {match.get('title', app_name)} בטלוויזיה"}

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    async def do_set_input(self, input_name: str = "") -> dict:
        """Switch HDMI input (e.g. 'HDMI1', 'HDMI2')."""
        if not input_name:
            return {"error": "חסר שם קלט"}
        conn = await self._ensure()
        data = await conn.request("ssap://tv/getExternalInputList")
        inputs = data.get("devices", [])
        input_lower = input_name.lower()
        match = None
        for inp in inputs:
            label = inp.get("label", "")
            if input_lower in label.lower() or input_lower in inp.get("id", "").lower():
                match = inp
                break
        if not match:
            names = [i.get("label", i.get("id", "?")) for i in inputs]
            return {"error": f"לא מצאתי '{input_name}'", "available": names}

        await conn.request("ssap://tv/switchInput", {"inputId": match["id"]})
        return {"reply_to_user_hebrew": f"עברתי ל-{match.get('label', input_name)}"}

    # ------------------------------------------------------------------
    # Toast (show message on TV screen)
    # ------------------------------------------------------------------

    async def do_toast(self, message: str = "") -> dict:
        """Show a toast notification on the TV screen."""
        if not message:
            return {"error": "חסר הודעה"}
        conn = await self._ensure()
        await conn.request("ssap://system.notifications/createToast", {"message": message})
        return {"reply_to_user_hebrew": f"הודעה נשלחה למסך הטלוויזיה"}
