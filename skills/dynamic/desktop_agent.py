"""Desktop agent skill — full GUI automation: capture, click, type, scroll, hotkeys.

Wraps pyautogui + pygetwindow for OS-level desktop control that goes beyond
what screen_reader (vision only) and system_control (shell only) provide.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from core.skill_base import BaseSkill

logger = logging.getLogger(__name__)

SCREENSHOT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "screenshots"


def _ts_filename(prefix: str = "desktop") -> str:
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"


class DesktopAgentSkill(BaseSkill):
    name = "desktop_agent"
    description = (
        "Full desktop GUI automation — capture screen, click, move mouse, "
        "type text, press keys, hotkeys, scroll, and inspect windows. "
        "שליטה מלאה בשולחן העבודה: לחיצות, הקלדה, גלילה, צילום מסך."
    )

    RISK_MAP = {
        "capture": "medium",
        "capture_region": "medium",
        "click": "medium",
        "move_mouse": "low",
        "type_text": "medium",
        "press_key": "medium",
        "hotkey": "medium",
        "scroll": "low",
        "active_window": "low",
        "mouse_position": "low",
        "wait_for_window": "low",
    }

    def __init__(self):
        self._failsafe = True

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        try:
            return await method(**(params or {}))
        except Exception as e:
            logger.exception("desktop_agent.%s failed", action)
            return {"error": str(e)}

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _pag():
        import pyautogui
        return pyautogui

    @staticmethod
    def _pgw():
        import pygetwindow as gw
        return gw

    # ── 1. capture (full screen) ─────────────────────────────────────

    async def do_capture(self, question: str = "מה מוצג על המסך?") -> dict:
        """Take a full-screen screenshot and return the path for vision analysis."""
        pag = self._pag()
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SCREENSHOT_DIR / _ts_filename("desktop")
        img = await asyncio.to_thread(pag.screenshot)
        await asyncio.to_thread(img.save, str(path))
        logger.info("Desktop capture saved: %s", path)
        return {
            "status": "ok",
            "screenshot_path": str(path),
            "vision_attach_path": str(path),
            "vision_question": question,
            "reply_to_user_hebrew": "צילמתי את המסך.",
        }

    # ── 2. capture_region ────────────────────────────────────────────

    async def do_capture_region(self, x: int, y: int, width: int, height: int,
                                question: str = "מה מוצג באזור הזה?") -> dict:
        """Capture a rectangular region of the screen (pixels from top-left)."""
        x, y, width, height = int(x), int(y), int(width), int(height)
        if width <= 0 or height <= 0:
            return {"error": "width and height must be positive."}
        if x < 0 or y < 0:
            return {"error": "x and y must be non-negative."}
        pag = self._pag()
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SCREENSHOT_DIR / _ts_filename("region")
        img = await asyncio.to_thread(pag.screenshot, region=(x, y, width, height))
        await asyncio.to_thread(img.save, str(path))
        logger.info("Region capture saved: %s", path)
        return {
            "status": "ok",
            "screenshot_path": str(path),
            "vision_attach_path": str(path),
            "vision_question": question,
            "region": {"x": x, "y": y, "width": width, "height": height},
        }

    # ── 3. click ─────────────────────────────────────────────────────

    async def do_click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> dict:
        """Click at pixel coordinates. button: left/right/middle. clicks: 1=single, 2=double."""
        x, y, clicks = int(x), int(y), int(clicks)
        if button not in ("left", "right", "middle"):
            return {"error": "button must be left, right, or middle."}
        pag = self._pag()
        await asyncio.to_thread(pag.click, x, y, clicks=clicks, button=button)
        logger.info("Clicked %s at (%d, %d) x%d", button, x, y, clicks)
        return {
            "status": "ok",
            "x": x, "y": y, "button": button, "clicks": clicks,
            "reply_to_user_hebrew": f"לחצתי ב-({x}, {y}).",
        }

    # ── 4. move_mouse ────────────────────────────────────────────────

    async def do_move_mouse(self, x: int, y: int, duration: float = 0.3) -> dict:
        """Move the mouse cursor to (x, y) over *duration* seconds."""
        x, y = int(x), int(y)
        duration = float(duration)
        pag = self._pag()
        await asyncio.to_thread(pag.moveTo, x, y, duration=duration)
        logger.info("Moved mouse to (%d, %d)", x, y)
        return {"status": "ok", "x": x, "y": y}

    # ── 5. type_text ─────────────────────────────────────────────────

    async def do_type_text(self, text: str, interval: float = 0.03) -> dict:
        """Type a string character by character (like a keyboard). interval is seconds between keys."""
        interval = float(interval)
        pag = self._pag()
        await asyncio.to_thread(pag.typewrite, text, interval=interval)
        logger.info("Typed %d chars", len(text))
        return {
            "status": "ok",
            "chars_typed": len(text),
            "reply_to_user_hebrew": f"הקלדתי {len(text)} תווים.",
        }

    # ── 6. press_key ─────────────────────────────────────────────────

    async def do_press_key(self, key: str, presses: int = 1) -> dict:
        """Press a single key (e.g. 'enter', 'tab', 'escape', 'f5', 'backspace')."""
        presses = int(presses)
        pag = self._pag()
        await asyncio.to_thread(pag.press, key, presses=presses)
        logger.info("Pressed '%s' x%d", key, presses)
        return {"status": "ok", "key": key, "presses": presses}

    # ── 7. hotkey ────────────────────────────────────────────────────

    async def do_hotkey(self, keys: str) -> dict:
        """Press a keyboard shortcut. keys is '+'-separated, e.g. 'ctrl+c', 'alt+f4', 'ctrl+shift+s'."""
        parts = [k.strip() for k in keys.split("+") if k.strip()]
        if not parts:
            return {"error": "No keys provided. Use format like 'ctrl+c'."}
        pag = self._pag()
        await asyncio.to_thread(pag.hotkey, *parts)
        logger.info("Hotkey: %s", "+".join(parts))
        return {
            "status": "ok",
            "keys": parts,
            "reply_to_user_hebrew": f"לחצתי {'+'.join(parts)}.",
        }

    # ── 8. scroll ────────────────────────────────────────────────────

    async def do_scroll(self, amount: int, x: int = 0, y: int = 0) -> dict:
        """Scroll the mouse wheel. Positive = up, negative = down. Optional (x,y) position."""
        amount, x, y = int(amount), int(x), int(y)
        pag = self._pag()
        kwargs = {"clicks": amount}
        if x or y:
            kwargs["x"] = x
            kwargs["y"] = y
        await asyncio.to_thread(pag.scroll, **kwargs)
        direction = "up" if amount > 0 else "down"
        logger.info("Scrolled %s by %d at (%d,%d)", direction, abs(amount), x, y)
        return {"status": "ok", "amount": amount, "x": x, "y": y}

    # ── 9. active_window ─────────────────────────────────────────────

    async def do_active_window(self) -> dict:
        """Return info about the currently active (foreground) window."""
        gw = self._pgw()
        win = await asyncio.to_thread(gw.getActiveWindow)
        if win is None:
            return {"status": "ok", "window": None, "reply_to_user_hebrew": "אין חלון פעיל כרגע."}
        return {
            "status": "ok",
            "window": {
                "title": win.title,
                "x": win.left,
                "y": win.top,
                "width": win.width,
                "height": win.height,
            },
            "reply_to_user_hebrew": f"החלון הפעיל: {win.title}",
        }

    # ── 10. mouse_position ───────────────────────────────────────────

    async def do_mouse_position(self) -> dict:
        """Return the current mouse cursor coordinates."""
        pag = self._pag()
        pos = await asyncio.to_thread(pag.position)
        return {"status": "ok", "x": pos[0], "y": pos[1]}

    # ── 11. wait_for_window ──────────────────────────────────────────

    async def do_wait_for_window(self, title: str, timeout: int = 10) -> dict:
        """Wait up to *timeout* seconds for a window whose title contains *title*."""
        timeout = int(timeout)
        gw = self._pgw()
        title_lower = title.lower()
        for _ in range(timeout * 4):  # check every 250ms
            windows = await asyncio.to_thread(gw.getWindowsWithTitle, title)
            # pygetwindow is case-sensitive, also try lowercase match
            if not windows:
                all_wins = await asyncio.to_thread(gw.getAllWindows)
                windows = [w for w in all_wins if title_lower in w.title.lower()]
            if windows:
                win = windows[0]
                return {
                    "status": "ok",
                    "found": True,
                    "title": win.title,
                    "reply_to_user_hebrew": f"מצאתי חלון: {win.title}",
                }
            await asyncio.sleep(0.25)
        return {
            "status": "ok",
            "found": False,
            "reply_to_user_hebrew": f"לא מצאתי חלון עם '{title}' תוך {timeout} שניות.",
        }
