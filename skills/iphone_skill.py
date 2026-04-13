"""iPhone push notification skill — sends alerts via the Pushover API.

Required env vars (set in .env with JARVIS_ prefix):
    JARVIS_PUSHOVER_USER_KEY   — your Pushover user/group key
    JARVIS_PUSHOVER_APP_TOKEN  — your Pushover application API token
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from config import get_settings
from core.skill_base import BaseSkill

logger = logging.getLogger(__name__)

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"

_PRIORITY_MAP = {
    "lowest": -2,
    "low": -1,
    "normal": 0,
    "high": 1,
    "emergency": 2,
}


class IPhoneSkill(BaseSkill):
    name = "iphone"
    description = (
        "Send push notifications to the iPhone via Pushover. "
        "Supports normal alerts, image attachments, and critical/high-priority notifications that bypass DND."
    )

    RISK_MAP = {
        "send_notification": "low",
        "send_image": "low",
        "send_critical": "low",
    }

    def __init__(self) -> None:
        s = get_settings()
        self._user_key = s.pushover_user_key
        self._app_token = s.pushover_app_token

    # ------------------------------------------------------------------
    # BaseSkill interface
    # ------------------------------------------------------------------

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        try:
            return await method(**(params or {}))
        except Exception as e:
            logger.exception("iphone.%s failed", action)
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def do_send_notification(
        self,
        title: str,
        message: str,
        priority: str = "normal",
    ) -> dict:
        """Send a push notification.

        Args:
            title: Notification title.
            message: Notification body text.
            priority: One of lowest / low / normal / high / emergency (default: normal).
        """
        prio_int = _PRIORITY_MAP.get(priority.lower(), 0)
        payload: dict = {
            "token": self._app_token,
            "user": self._user_key,
            "title": title,
            "message": message,
            "priority": prio_int,
        }
        # Emergency (2) requires retry + expire parameters
        if prio_int == 2:
            payload.setdefault("retry", 30)
            payload.setdefault("expire", 3600)

        return await self._post(payload)

    async def do_send_image(
        self,
        title: str,
        message: str,
        image_path: str,
    ) -> dict:
        """Send a push notification with an image attachment.

        Args:
            title: Notification title.
            message: Notification body text.
            image_path: Absolute or relative path to the image file (JPEG/PNG/GIF, max 2.5 MB).
        """
        path = Path(image_path)
        if not path.exists():
            return {"error": f"Image not found: {image_path}"}

        payload = {
            "token": self._app_token,
            "user": self._user_key,
            "title": title,
            "message": message,
            "priority": 0,
        }

        async with httpx.AsyncClient(timeout=30) as client:
            with path.open("rb") as fh:
                r = await client.post(
                    PUSHOVER_API_URL,
                    data=payload,
                    files={"attachment": (path.name, fh, _mime_for(path))},
                )
        return _parse_response(r)

    async def do_send_critical(
        self,
        title: str,
        message: str,
    ) -> dict:
        """Send a high-priority notification that bypasses Do-Not-Disturb.

        Uses Pushover priority 1 (high) so the device makes noise even in
        silent/DND mode without requiring acknowledgement (use priority 2 /
        emergency for that). To escalate to emergency with retry, call
        do_send_notification with priority='emergency' instead.

        Args:
            title: Notification title.
            message: Notification body text.
        """
        payload = {
            "token": self._app_token,
            "user": self._user_key,
            "title": title,
            "message": message,
            "priority": 1,
        }
        return await self._post(payload)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _post(self, payload: dict) -> dict:
        if not self._user_key or not self._app_token:
            return {
                "error": (
                    "Pushover credentials not configured. "
                    "Set JARVIS_PUSHOVER_USER_KEY and JARVIS_PUSHOVER_APP_TOKEN in .env."
                )
            }
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(PUSHOVER_API_URL, data=payload)
        return _parse_response(r)


def _parse_response(r: httpx.Response) -> dict:
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text}

    if r.status_code == 200 and body.get("status") == 1:
        return {"status": "ok", "request": body.get("request")}

    logger.warning("Pushover error %s: %s", r.status_code, body)
    return {"error": body.get("errors", r.text), "status_code": r.status_code}


def _mime_for(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
    }.get(ext, "application/octet-stream")
