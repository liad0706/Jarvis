"""Screen reader skill -- capture the screen and use vision models to understand it.

הבנת מסך -- צילום מסך ושימוש במודלי ראייה להבנת התוכן המוצג.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

from core.skill_base import BaseSkill

logger = logging.getLogger(__name__)

# Directory for saving screenshots
SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "data" / "screenshots"


def _ensure_screenshot_dir() -> Path:
    """Create the screenshots directory if it doesn't exist."""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    return SCREENSHOT_DIR


def _generate_filename(prefix: str = "screen") -> str:
    """Generate a timestamped filename for a screenshot."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{ts}.png"


class ScreenReaderSkill(BaseSkill):
    """Capture the screen and leverage vision models to understand what's displayed."""

    name = "screen_reader"
    description = (
        "Capture screenshots and use the vision model to describe, read, "
        "or answer questions about what's on screen. "
        "צילום מסך והבנת התוכן באמצעות מודל ראייה."
    )

    RISK_MAP = {
        "capture_and_describe": "medium",
        "read_text_from_screen": "medium",
        "capture_region": "medium",
    }

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        try:
            return await method(**(params or {}))
        except Exception as e:
            logger.exception("screen_reader.%s failed", action)
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_pyautogui():
        try:
            import pyautogui
            return pyautogui
        except ImportError:
            raise ImportError(
                "pyautogui is required for screenshots. Install with: pip install pyautogui"
            )

    def _take_screenshot(self, region=None) -> Path:
        """Take a screenshot (full or region) and save to data/screenshots/."""
        pyautogui = self._get_pyautogui()
        _ensure_screenshot_dir()

        prefix = "region" if region else "screen"
        filename = _generate_filename(prefix)
        save_path = SCREENSHOT_DIR / filename

        if region:
            img = pyautogui.screenshot(region=region)
        else:
            img = pyautogui.screenshot()

        img.save(str(save_path))
        logger.info("Screenshot saved: %s", save_path)
        return save_path

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------

    async def do_capture_and_describe(
        self, question: str = "\u05de\u05d4 \u05de\u05d5\u05e6\u05d2 \u05e2\u05dc \u05d4\u05de\u05e1\u05da?"
    ) -> dict:
        """Take a screenshot and ask the vision model to describe what's on screen. The question defaults to 'מה מוצג על המסך?' (What's on screen?). צילום מסך ותיאור התוכן."""
        loop = asyncio.get_event_loop()
        screenshot_path = await loop.run_in_executor(None, self._take_screenshot)

        return {
            "status": "ok",
            "screenshot_path": str(screenshot_path),
            "vision_attach_path": str(screenshot_path),
            "vision_question": question,
            "message": (
                f"Screenshot captured. Please look at the image and answer: {question}"
            ),
        }

    async def do_read_text_from_screen(self) -> dict:
        """Take a screenshot and ask the vision model to read all visible text. צילום מסך וקריאת טקסט."""
        loop = asyncio.get_event_loop()
        screenshot_path = await loop.run_in_executor(None, self._take_screenshot)

        return {
            "status": "ok",
            "screenshot_path": str(screenshot_path),
            "vision_attach_path": str(screenshot_path),
            "vision_question": (
                "Please read and transcribe ALL visible text on this screen, "
                "including menus, buttons, labels, and content. "
                "Preserve the layout as much as possible. "
                "If there is Hebrew text, include it as-is."
            ),
            "message": "Screenshot captured. Please read all visible text from the image.",
        }

    async def do_capture_region(
        self, x: int, y: int, width: int, height: int
    ) -> dict:
        """Capture a specific region of the screen. Coordinates are in pixels from top-left. צילום אזור מסוים במסך."""
        x, y, width, height = int(x), int(y), int(width), int(height)

        if width <= 0 or height <= 0:
            return {"error": "Width and height must be positive integers."}
        if x < 0 or y < 0:
            return {"error": "x and y coordinates must be non-negative."}

        region = (x, y, width, height)
        loop = asyncio.get_event_loop()
        screenshot_path = await loop.run_in_executor(
            None, self._take_screenshot, region
        )

        return {
            "status": "ok",
            "screenshot_path": str(screenshot_path),
            "vision_attach_path": str(screenshot_path),
            "region": {"x": x, "y": y, "width": width, "height": height},
            "vision_question": "מה מוצג באזור הזה של המסך?",
            "message": f"Region captured ({width}x{height} at ({x},{y})). Image ready for vision model.",
        }
