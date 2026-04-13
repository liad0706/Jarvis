"""Restart skill - allows Jarvis to restart itself and resume where it left off."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from core.skill_base import BaseSkill

logger = logging.getLogger(__name__)

RESTART_FILE = Path(__file__).resolve().parent.parent / "data" / "restart_context.json"


def save_restart_context(
    reason: str = "",
    resume_message: str = "",
    source: str = "manual",
    changed_files: list[str] | None = None,
) -> dict:
    """Persist restart metadata so the next process can explain why it restarted."""
    context = {
        "reason": reason or "Code changes applied",
        "resume_message": resume_message or "Jarvis restarted successfully.",
        "source": source or "manual",
        "changed_files": list(changed_files or []),
        "timestamp": time.time(),
    }
    RESTART_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESTART_FILE.write_text(
        json.dumps(context, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return context


def has_pending_restart() -> dict | None:
    """Check if there's a pending restart context. Called on boot by main.py."""
    if not RESTART_FILE.exists():
        return None
    try:
        data = json.loads(RESTART_FILE.read_text(encoding="utf-8"))
        age = time.time() - data.get("timestamp", 0)
        if age > 300:
            RESTART_FILE.unlink(missing_ok=True)
            return None
        return data
    except Exception:
        RESTART_FILE.unlink(missing_ok=True)
        return None


def clear_restart_context():
    """Remove restart context after successful recovery."""
    RESTART_FILE.unlink(missing_ok=True)


class RestartSkill(BaseSkill):
    name = "restart"
    description = (
        "Restart Jarvis to apply code changes. Jarvis saves what it was doing and "
        "resumes automatically after a fresh process restart."
    )
    RISK_MAP = {"restart": "medium"}

    def __init__(self):
        self._shutdown_callback = None

    def set_shutdown_callback(self, callback):
        """Set by main.py - the function that triggers graceful shutdown + restart."""
        self._shutdown_callback = callback

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        try:
            return await method(**(params or {}))
        except Exception as e:
            return {"error": f"Restart failed: {e}"}

    async def do_restart(self, reason: str = "", resume_message: str = "") -> dict:
        """Restart Jarvis to apply code changes."""
        if not self._shutdown_callback:
            return {"error": "Restart not available - shutdown callback not configured."}

        save_restart_context(
            reason=reason,
            resume_message=resume_message or "Jarvis restarted successfully.",
            source="skill",
        )
        logger.info("Restart context saved: %s", reason or "manual restart")

        try:
            self._shutdown_callback(reason=reason, resume_message=resume_message, source="skill")
        except TypeError:
            self._shutdown_callback()

        return {
            "status": "restarting",
            "message": "Jarvis is restarting...",
            "reply_to_user_hebrew": "מפעיל מחדש ועולה עם הקוד החדש.",
        }
