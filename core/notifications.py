"""Notification system — desktop toasts + EventBus + WhatsApp push."""

from __future__ import annotations
import asyncio
import logging
import time
from collections import deque
from enum import Enum
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


class NotificationLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    URGENT = "urgent"
    SUCCESS = "success"


class Notification:
    def __init__(self, title: str, message: str, level: NotificationLevel = NotificationLevel.INFO,
                 source: str = "", action_url: str = ""):
        self.title = title
        self.message = message
        self.level = level
        self.source = source
        self.action_url = action_url
        self.timestamp = time.time()
        self.read = False
        self.id = f"n_{int(self.timestamp * 1000)}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "message": self.message,
            "level": self.level.value,
            "source": self.source,
            "action_url": self.action_url,
            "timestamp": self.timestamp,
            "read": self.read,
        }


class NotificationManager:
    """Central notification hub — routes to desktop, dashboard, WhatsApp."""

    MAX_HISTORY = 100

    def __init__(self, event_bus=None):
        self.event_bus = event_bus
        self._history: deque[Notification] = deque(maxlen=self.MAX_HISTORY)
        self._desktop_enabled = True
        self._handlers: list[Callable] = []

    def add_handler(self, handler: Callable):
        """Add a custom notification handler (e.g., WhatsApp push)."""
        self._handlers.append(handler)

    async def notify(self, title: str, message: str,
                     level: NotificationLevel = NotificationLevel.INFO,
                     source: str = "", action_url: str = "") -> Notification:
        """Send a notification through all channels."""
        notif = Notification(title, message, level, source, action_url)
        self._history.append(notif)

        # Desktop toast
        if self._desktop_enabled:
            await self._desktop_notify(notif)

        # EventBus (for dashboard WebSocket)
        if self.event_bus:
            await self.event_bus.emit("notification", **notif.to_dict())

        # Custom handlers
        for handler in self._handlers:
            try:
                result = handler(notif)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.debug("Notification handler failed: %s", e)

        logger.info("Notification [%s]: %s - %s", level.value, title, message)
        return notif

    async def _desktop_notify(self, notif: Notification):
        """Show a Windows desktop toast notification."""
        try:
            import asyncio
            def _show():
                try:
                    # Try win10toast first
                    from win10toast import ToastNotifier
                    toaster = ToastNotifier()
                    toaster.show_toast(
                        notif.title,
                        notif.message,
                        duration=5,
                        threaded=True,
                    )
                except ImportError:
                    try:
                        # Fallback: plyer
                        from plyer import notification as plyer_notif
                        plyer_notif.notify(
                            title=notif.title,
                            message=notif.message,
                            timeout=5,
                        )
                    except ImportError:
                        # Last resort: PowerShell toast
                        import subprocess
                        ps_script = (
                            f'[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; '
                            f'$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); '
                            f'$textNodes = $template.GetElementsByTagName("text"); '
                            f'$textNodes.Item(0).AppendChild($template.CreateTextNode("{notif.title}")) | Out-Null; '
                            f'$textNodes.Item(1).AppendChild($template.CreateTextNode("{notif.message}")) | Out-Null; '
                            f'$toast = [Windows.UI.Notifications.ToastNotification]::new($template); '
                            f'[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Jarvis").Show($toast);'
                        )
                        subprocess.Popen(
                            ["powershell", "-Command", ps_script],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
            await asyncio.to_thread(_show)
        except Exception as e:
            logger.debug("Desktop notification failed: %s", e)

    def get_history(self, unread_only: bool = False, limit: int = 50) -> list[dict]:
        """Get notification history."""
        items = list(self._history)
        if unread_only:
            items = [n for n in items if not n.read]
        items = sorted(items, key=lambda n: n.timestamp, reverse=True)[:limit]
        return [n.to_dict() for n in items]

    def mark_read(self, notification_id: str) -> bool:
        """Mark a notification as read."""
        for n in self._history:
            if n.id == notification_id:
                n.read = True
                return True
        return False

    def mark_all_read(self):
        """Mark all notifications as read."""
        for n in self._history:
            n.read = True

    def unread_count(self) -> int:
        return sum(1 for n in self._history if not n.read)
