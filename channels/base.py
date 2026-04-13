"""Base class for all Jarvis communication channels."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


@dataclass
class IncomingMessage:
    """Represents a message received from any channel."""
    text: str
    sender_id: str
    sender_name: str = ""
    channel_name: str = ""
    channel_id: str = ""
    is_group: bool = False
    reply_to: str | None = None
    attachments: list[str] = field(default_factory=list)
    raw: Any = None


@dataclass
class OutgoingMessage:
    """Represents a message to send through a channel."""
    text: str
    recipient_id: str = ""
    channel_id: str = ""
    reply_to: str | None = None
    attachments: list[str] = field(default_factory=list)


# Type for the handler function: receives IncomingMessage, returns response text
MessageHandler = Callable[[IncomingMessage], Awaitable[str]]


class BaseChannel(ABC):
    """Abstract base for communication channels."""

    name: str = "base"
    enabled: bool = False

    @abstractmethod
    async def start(self, handler: MessageHandler) -> None:
        """Start listening for messages. Call handler for each incoming message."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel gracefully."""
        ...

    @abstractmethod
    async def send(self, message: OutgoingMessage) -> bool:
        """Send a message through this channel. Returns True on success."""
        ...

    async def send_text(self, text: str, recipient_id: str = "", channel_id: str = "") -> bool:
        """Convenience: send plain text."""
        return await self.send(OutgoingMessage(
            text=text, recipient_id=recipient_id, channel_id=channel_id,
        ))
