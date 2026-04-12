"""Apple TV control via pyatv — discover, playback status, protocol pairing."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from config import get_settings
from core.skill_base import BaseSkill

logger = logging.getLogger(__name__)


class AppleTVSkill(BaseSkill):
    name = "apple_tv"
    description = (
        "Apple TV / ממיר on LAN: discover, pairing_status (check Companion+AirPlay credentials), "
        "power_off/power_on, playback status, pair Companion/AirPlay/RAOP, "
        "list_apps (see installed apps), launch_app (open app by app_id). Must pair before power_off works."
    )

    RISK_MAP = {
        "discover": "low",
        "status": "low",
        "pairing_status": "low",
        "pair_protocol": "high",
        "power_off": "high",
        "power_on": "high",
        "launch_app": "medium",
        "list_apps": "low",
    }

    def __init__(self):
        self.settings = get_settings()

    def _cred_path(self) -> Path:
        return Path(self.settings.apple_tv_credentials_file).expanduser()

    def _host(self, host: str = "") -> str:
        h = (host or "").strip()
        # LLM sometimes passes the env var name instead of the actual IP — ignore it
        if h and not all(c.isdigit() or c == "." for c in h):
            h = ""
        return (h or self.settings.apple_tv_host or "").strip()

    async def _storage(self, loop: asyncio.AbstractEventLoop):
        from pyatv.storage.file_storage import FileStorage

        p = self._cred_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        st = FileStorage(p.as_posix(), loop)
        await st.load()
        return st

    async def _scan_one(self, loop: asyncio.AbstractEventLoop, host: str, storage):
        from pyatv import scan

        atvs = await scan(loop, hosts=[host], storage=storage, timeout=15)
        return atvs[0] if atvs else None

    async def _close_atv(self, atv) -> None:
        pending = atv.close()
        if pending:
            await asyncio.gather(*pending)

    async def _companion_credentials_ok(self, storage, conf) -> bool:
        """True if storage has Companion pairing credentials (required for power/remote on modern tvOS)."""
        try:
            settings = await storage.get_settings(conf)
            c = settings.protocols.companion.credentials
            return bool(c and str(c).strip())
        except Exception:
            return False

    def _pairing_help(self, host: str) -> dict:
        cred = self._cred_path()
        wizard = f'atvremote -s {host} --storage-filename "{cred}" wizard'
        pair_companion = (
            f'atvremote -s {host} --storage-filename "{cred}" --protocol companion pair'
        )
        pair_airplay = f'atvremote -s {host} --storage-filename "{cred}" --protocol airplay pair'
        return {
            "error": "no_companion_credentials",
            "atvremote_wizard": wizard,
            "atvremote_pair_companion": pair_companion,
            "atvremote_pair_airplay": pair_airplay,
            "reply_to_user_hebrew": (
                "הממיר לא מזווג. לפקודת pair חובה להוסיף --protocol (או להשתמש ב-wizard). "
                f"הכי קל: {wizard} — בחר מספר מכשיר והזן קוד מהמסך. "
                "אחרת: קודם --protocol companion pair, אחר כך --protocol airplay pair."
            ),
        }

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        try:
            return await method(**(params or {}))
        except Exception as e:
            # Status checks fail often when Apple TV is asleep — log concisely
            if action == "status":
                logger.error("apple_tv.status failed: %s", e)
            else:
                logger.exception("apple_tv.%s failed", action)
            return {"error": str(e)}

    async def do_discover(self, host: str = "") -> dict:
        """Scan JARVIS_APPLE_TV_HOST (or pass host) — returns name, address, model, services."""
        h = self._host(host)
        if not h:
            return {"error": "הגדר JARVIS_APPLE_TV_HOST או העבר host"}

        try:
            import pyatv  # noqa: F401
        except ImportError:
            return {"error": "pyatv לא מותקן — pip install pyatv"}

        loop = asyncio.get_running_loop()
        storage = await self._storage(loop)
        conf = await self._scan_one(loop, h, storage)
        if not conf:
            return {"error": f"לא נמצא מכשיר ב-{h}", "host": h}

        services = []
        for s in conf.services:
            pr = getattr(s, "protocol", None)
            pair = getattr(s, "pairing", None)
            services.append(
                {
                    "protocol": pr.name if pr is not None else None,
                    "port": getattr(s, "port", None),
                    "pairing": pair.name if pair is not None else None,
                }
            )

        di = conf.device_info
        model_s = di.model.name if di and di.model else None

        return {
            "ok": True,
            "name": conf.name,
            "address": str(conf.address),
            "model": model_s,
            "version": di.version if di else None,
            "identifiers": list(conf.all_identifiers),
            "services": services,
            "reply_to_user_hebrew": f"נמצאה Apple TV: {conf.name} ({conf.address}).",
        }

    async def do_status(self, host: str = "") -> dict:
        """Connect (uses saved credentials if any) and return playback / idle state."""
        h = self._host(host)
        if not h:
            return {"error": "הגדר JARVIS_APPLE_TV_HOST או העבר host"}

        try:
            from pyatv import connect
        except ImportError:
            return {"error": "pyatv לא מותקן — pip install pyatv"}

        loop = asyncio.get_running_loop()
        storage = await self._storage(loop)
        conf = await self._scan_one(loop, h, storage)
        if not conf:
            return {"error": f"לא נמצא מכשיר ב-{h}"}

        atv = await connect(conf, loop, storage=storage)
        try:
            playing = await atv.metadata.playing()
            dev = atv.device_info
            mt = playing.media_type.name if playing.media_type else None
            ds = playing.device_state.name if playing.device_state else None
            out = {
                "ok": True,
                "device_name": conf.name,
                "media_type": mt,
                "device_state": ds,
                "title": playing.title,
                "artist": playing.artist,
            }
            if dev:
                out["os_version"] = dev.version
            out["reply_to_user_hebrew"] = (
                f"סטטוס {conf.name}: {ds or 'לא ידוע'}, "
                f"תוכן: {playing.title or 'אין'}"
            )
            return out
        finally:
            await self._close_atv(atv)
            await storage.save()

    async def do_power_off(self, host: str = "") -> dict:
        """Turn off the Apple TV (HDMI-CEC / standby when supported). Requires paired Companion (or power-capable protocol)."""
        h = self._host(host)
        if not h:
            return {"error": "הגדר JARVIS_APPLE_TV_HOST או העבר host"}

        try:
            from pyatv import connect
            from pyatv import exceptions as pex
        except ImportError:
            return {"error": "pyatv לא מותקן — pip install pyatv"}

        loop = asyncio.get_running_loop()
        storage = await self._storage(loop)
        conf = await self._scan_one(loop, h, storage)
        if not conf:
            return {"error": f"לא נמצא מכשיר ב-{h}"}

        if not await self._companion_credentials_ok(storage, conf):
            out = self._pairing_help(h)
            out["device_name"] = conf.name
            return out

        atv = await connect(conf, loop, storage=storage)
        try:
            try:
                await atv.power.turn_off(await_new_state=False)
                method = "power.turn_off"
            except pex.NotSupportedError:
                await atv.remote_control.suspend()
                method = "remote_control.suspend"
            return {
                "ok": True,
                "device_name": conf.name,
                "method": method,
                "reply_to_user_hebrew": f"שלחתי כיבוי/שינה לממיר ({conf.name}). אם הטלוויזיה נשארת דלוקה — ודא HDMI-CEC (הגדרות Apple TV וטלוויזיה).",
            }
        except pex.NotSupportedError as e:
            return {
                "error": str(e),
                "reply_to_user_hebrew": (
                    "המחשב מזווג אבל הממיר לא מאפשר כיבוי מרחוק (או חסר CEC). "
                    "נסה זיווג מחדש ל-Companion ו-AirPlay, או כבה ידנית מהשלט."
                ),
                "hint": "אם כבר יש זיווג: בדוק בטלוויזיה שהופעל HDMI-CEC / Consumer Electronic Control.",
            }
        finally:
            await self._close_atv(atv)
            await storage.save()

    async def do_power_on(self, host: str = "") -> dict:
        """Wake / turn on the Apple TV when the protocol supports it."""
        h = self._host(host)
        if not h:
            return {"error": "הגדר JARVIS_APPLE_TV_HOST או העבר host"}

        try:
            from pyatv import connect
            from pyatv import exceptions as pex
        except ImportError:
            return {"error": "pyatv לא מותקן — pip install pyatv"}

        loop = asyncio.get_running_loop()
        storage = await self._storage(loop)
        conf = await self._scan_one(loop, h, storage)
        if not conf:
            return {"error": f"לא נמצא מכשיר ב-{h}"}

        if not await self._companion_credentials_ok(storage, conf):
            out = self._pairing_help(h)
            out["device_name"] = conf.name
            return out

        atv = await connect(conf, loop, storage=storage)
        try:
            try:
                await atv.power.turn_on(await_new_state=False)
                method = "power.turn_on"
            except pex.NotSupportedError:
                await atv.remote_control.wakeup()
                method = "remote_control.wakeup"
            return {
                "ok": True,
                "device_name": conf.name,
                "method": method,
                "reply_to_user_hebrew": f"שלחתי הפעלה/השכמה לממיר ({conf.name}).",
            }
        except pex.NotSupportedError as e:
            return {
                "error": str(e),
                "hint": "הפעלה מרחוק דורשת זיווג Companion תקין.",
            }
        finally:
            await self._close_atv(atv)
            await storage.save()

    async def do_pairing_status(self, host: str = "") -> dict:
        """Report whether Companion (and AirPlay) credentials exist in storage — use before power_off."""
        h = self._host(host)
        if not h:
            return {"error": "הגדר JARVIS_APPLE_TV_HOST או העבר host"}
        try:
            import pyatv  # noqa: F401
        except ImportError:
            return {"error": "pyatv לא מותקן — pip install pyatv"}

        loop = asyncio.get_running_loop()
        storage = await self._storage(loop)
        conf = await self._scan_one(loop, h, storage)
        if not conf:
            return {"error": f"לא נמצא מכשיר ב-{h}"}

        settings = await storage.get_settings(conf)
        comp = bool(settings.protocols.companion.credentials and str(settings.protocols.companion.credentials).strip())
        ap = bool(settings.protocols.airplay.credentials and str(settings.protocols.airplay.credentials).strip())
        ok = comp and ap
        return {
            "ok": ok,
            "device_name": conf.name,
            "companion_paired": comp,
            "airplay_paired": ap,
            "credentials_file": str(self._cred_path()),
            "reply_to_user_hebrew": (
                "הממיר מזווג מול המחשב — אפשר לנסות כיבוי."
                if ok
                else (
                    f"חסר זיווג: Companion={'כן' if comp else 'לא'}, AirPlay={'כן' if ap else 'לא'}. "
                    "השלם זיווג לפני כיבוי מהמחשב."
                )
            ),
        }

    async def do_pair_protocol(self, protocol: str = "Companion", pin: str = "") -> dict:
        """Pair one protocol. If the TV shows a PIN, pass pin=digits. Else TV may ask for a PIN you choose (try 1234)."""
        h = self._host()
        if not h:
            return {"error": "הגדר JARVIS_APPLE_TV_HOST"}

        try:
            from pyatv import pair
            from pyatv.const import Protocol
        except ImportError:
            return {"error": "pyatv לא מותקן — pip install pyatv"}

        key = (protocol or "Companion").strip().lower()
        proto_map = {
            "companion": Protocol.Companion,
            "airplay": Protocol.AirPlay,
            "raop": Protocol.RAOP,
        }
        proto = proto_map.get(key)
        if proto is None:
            return {"error": f"פרוטוקול לא נתמך: {protocol}. בחר: Companion, AirPlay, RAOP"}

        loop = asyncio.get_running_loop()
        storage = await self._storage(loop)
        conf = await self._scan_one(loop, h, storage)
        if not conf:
            return {"error": f"לא נמצא מכשיר ב-{h}"}

        pairing = await pair(conf, proto, loop, storage=storage)
        try:
            await pairing.begin()
            if pairing.device_provides_pin:
                pin_str = (pin or "").strip()
                if not pin_str:
                    return {
                        "error": "הטלוויזיה מציגה קוד — הרץ שוב עם אותו פרוטוקול ו-pin=הקוד",
                        "device_provides_pin": True,
                    }
                pairing.pin(pin_str)
            else:
                use = (pin or "1234").strip()
                if use.isdigit():
                    pairing.pin(int(use))
                else:
                    pairing.pin(use)
            await pairing.finish()
            if pairing.has_paired:
                await storage.save()
                return {
                    "ok": True,
                    "protocol": proto.name,
                    "reply_to_user_hebrew": f"זיווג {proto.name} הצליח. אם השלט עדיין לא עובד, זווג גם את הפרוטוקולים האחרים (AirPlay וכו').",
                }
            return {"error": "הזיווג לא הושלם — בדוק קוד או נסה שוב.", "protocol": proto.name}
        finally:
            await pairing.close()

    async def do_list_apps(self, host: str = "") -> dict:
        """List installed apps on Apple TV."""
        h = self._host(host)
        if not h:
            return {"error": "הגדר JARVIS_APPLE_TV_HOST או העבר host"}

        try:
            from pyatv import connect
        except ImportError:
            return {"error": "pyatv לא מותקן — pip install pyatv"}

        loop = asyncio.get_running_loop()
        storage = await self._storage(loop)
        conf = await self._scan_one(loop, h, storage)
        if not conf:
            return {"error": f"לא נמצא מכשיר ב-{h}"}

        atv = await connect(conf, loop, storage=storage)
        try:
            app_list = await atv.apps.app_list()
            apps = [{"name": app.name, "id": app.identifier} for app in app_list]
            names = "\n".join(f"• {a['name']} ({a['id']})" for a in apps[:20])
            return {
                "apps": apps,
                "reply_to_user_hebrew": f"אפליקציות בממיר:\n{names}",
            }
        except Exception as e:
            logger.exception("list_apps failed")
            return {"error": str(e), "reply_to_user_hebrew": "לא הצלחתי לקבל רשימת אפליקציות מהממיר."}
        finally:
            await self._close_atv(atv)
            await storage.save()

    async def do_launch_app(self, app_id: str = "", host: str = "") -> dict:
        """Launch an app on Apple TV by bundle ID (e.g. com.apple.TVShows, com.spotify.client).
        Use list_apps first to get the correct app_id."""
        h = self._host(host)
        if not h:
            return {"error": "הגדר JARVIS_APPLE_TV_HOST או העבר host"}
        if not app_id:
            return {"error": "חסר app_id — השתמש ב-list_apps כדי לקבל את ה-ID הנכון"}

        try:
            from pyatv import connect
        except ImportError:
            return {"error": "pyatv לא מותקן — pip install pyatv"}

        loop = asyncio.get_running_loop()
        storage = await self._storage(loop)
        conf = await self._scan_one(loop, h, storage)
        if not conf:
            return {"error": f"לא נמצא מכשיר ב-{h}"}

        atv = await connect(conf, loop, storage=storage)
        try:
            await atv.apps.launch_app(app_id)
            return {
                "ok": True,
                "app_id": app_id,
                "reply_to_user_hebrew": f"הפעלתי את האפליקציה {app_id} בממיר.",
            }
        except Exception as e:
            logger.exception("launch_app failed for %s", app_id)
            return {"error": str(e), "reply_to_user_hebrew": f"לא הצלחתי לפתוח {app_id} בממיר: {e}"}
        finally:
            await self._close_atv(atv)
            await storage.save()
