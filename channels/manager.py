"""Channel Manager — starts and manages all communication channels."""

from __future__ import annotations

import logging
from typing import Any

from channels.base import BaseChannel, IncomingMessage, MessageHandler

logger = logging.getLogger(__name__)


class ChannelManager:
    """Manages all communication channels (Discord, Telegram, WhatsApp, etc.)."""

    def __init__(self):
        self._channels: dict[str, BaseChannel] = {}

    def register(self, channel: BaseChannel):
        self._channels[channel.name] = channel
        logger.info("Channel registered: %s (enabled=%s)", channel.name, channel.enabled)

    async def start_all(self, handler: MessageHandler):
        """Start all enabled channels with the given message handler."""
        for name, channel in self._channels.items():
            if channel.enabled:
                try:
                    await channel.start(handler)
                    logger.info("Channel started: %s", name)
                except Exception as e:
                    logger.error("Channel %s failed to start: %s", name, e)

    async def stop_all(self):
        for name, channel in self._channels.items():
            try:
                await channel.stop()
            except Exception as e:
                logger.warning("Channel %s stop error: %s", name, e)

    def get(self, name: str) -> BaseChannel | None:
        return self._channels.get(name)

    @property
    def active_channels(self) -> list[str]:
        return [n for n, c in self._channels.items() if c.enabled]


def create_channel_manager(settings) -> ChannelManager:
    """Create a ChannelManager with all configured channels."""
    manager = ChannelManager()

    # Discord
    if settings.discord_token:
        from channels.discord_channel import DiscordChannel
        manager.register(DiscordChannel(
            token=settings.discord_token,
            allowed_users=settings.discord_allowed_users,
            allowed_channels=settings.discord_allowed_channels,
        ))

    # Telegram
    if settings.telegram_token:
        from channels.telegram_channel import TelegramChannel
        manager.register(TelegramChannel(
            token=settings.telegram_token,
            allowed_users=settings.telegram_allowed_users,
        ))

    return manager
