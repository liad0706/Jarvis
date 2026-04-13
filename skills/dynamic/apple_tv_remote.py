"""Apple TV remote control — send button presses to a paired Apple TV."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from core.skill_base import BaseSkill
from config import get_settings

logger = logging.getLogger(__name__)

BUTTON_MAP = {
    "select": "select",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "menu": "menu",
    "home": "home",
    "play_pause": "play_pause",
    "back": "back",
}


class AppleTVRemoteSkill(BaseSkill):
    name = "apple_tv_remote"
    description = "Control your paired Apple TV with Jarvis"
    REQUIREMENTS: list[str] = ["pyatv"]

    def __init__(self):
        self.settings = get_settings()

    async def _connect(self):
        """Connect to the Apple TV using stored credentials."""
        from pyatv import scan, connect
        from pyatv.storage.file_storage import FileStorage

        host = (self.settings.apple_tv_host or "").strip()
        if not host:
            raise RuntimeError("JARVIS_APPLE_TV_HOST not set")

        cred_path = Path(self.settings.apple_tv_credentials_file).expanduser()
        loop = asyncio.get_running_loop()
        storage = FileStorage(cred_path.as_posix(), loop)
        await storage.load()

        atvs = await scan(loop, hosts=[host], storage=storage, timeout=15)
        if not atvs:
            raise RuntimeError(f"Apple TV not found at {host}")

        atv = await connect(atvs[0], loop, storage=storage)
        return atv, storage

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        try:
            return await method(**(params or {}))
        except Exception as e:
            logger.exception("apple_tv_remote.%s failed", action)
            return {"error": str(e)}

    async def _press(self, button: str) -> dict:
        """Generic button press — connect, press, disconnect."""
        atv, storage = await self._connect()
        try:
            rc = atv.remote_control
            press_fn = getattr(rc, button, None)
            if not press_fn:
                return {"error": f"Button '{button}' not supported by pyatv remote_control"}
            await press_fn()
            return {"status": "ok", "button": button}
        finally:
            pending = atv.close()
            if pending:
                await asyncio.gather(*pending)
            await storage.save()

    async def do_select(self) -> dict:
        return await self._press("select")

    async def do_up(self) -> dict:
        return await self._press("up")

    async def do_down(self) -> dict:
        return await self._press("down")

    async def do_left(self) -> dict:
        return await self._press("left")

    async def do_right(self) -> dict:
        return await self._press("right")

    async def do_menu(self) -> dict:
        return await self._press("menu")

    async def do_home(self) -> dict:
        return await self._press("home")

    async def do_play_pause(self) -> dict:
        return await self._press("play_pause")

    async def do_back(self) -> dict:
        return await self._press("back")
