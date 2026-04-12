"""Discord channel — connects Jarvis to a Discord bot.

Requires: pip install discord.py
Set JARVIS_DISCORD_TOKEN in .env to your bot token.
Optionally set JARVIS_DISCORD_ALLOWED_USERS (comma-separated user IDs).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from channels.base import BaseChannel, IncomingMessage, OutgoingMessage, MessageHandler

logger = logging.getLogger(__name__)


class DiscordChannel(BaseChannel):
    name = "discord"

    def __init__(self, token: str = "", allowed_users: str = "", allowed_channels: str = ""):
        self.token = token
        self.allowed_user_ids: set[str] = {
            u.strip() for u in allowed_users.split(",") if u.strip()
        }
        self.allowed_channel_ids: set[str] = {
            c.strip() for c in allowed_channels.split(",") if c.strip()
        }
        self._client: Any = None
        self._handler: MessageHandler | None = None
        self._task: asyncio.Task | None = None
        self.enabled = bool(token)

    async def start(self, handler: MessageHandler) -> None:
        if not self.token:
            logger.warning("Discord: no token configured, skipping")
            return

        try:
            import discord
        except ImportError:
            logger.error("Discord: discord.py not installed. Run: pip install discord.py")
            return

        self._handler = handler
        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)

        @self._client.event
        async def on_ready():
            logger.info("Discord: logged in as %s (ID: %s)", self._client.user.name, self._client.user.id)

        @self._client.event
        async def on_message(message: discord.Message):
            # Don't respond to ourselves
            if message.author == self._client.user:
                return

            # Filter by allowed users
            if self.allowed_user_ids and str(message.author.id) not in self.allowed_user_ids:
                return

            # Filter by allowed channels
            if self.allowed_channel_ids and str(message.channel.id) not in self.allowed_channel_ids:
                return

            # Check if bot was mentioned or DM
            is_dm = message.guild is None
            is_mentioned = self._client.user in message.mentions
            # In DMs always respond; in servers only when mentioned or prefixed with "jarvis"
            content = message.content.strip()
            if not is_dm and not is_mentioned:
                if not content.lower().startswith("jarvis"):
                    return
                content = content[6:].strip().lstrip(",").strip()

            if is_mentioned:
                content = content.replace(f"<@{self._client.user.id}>", "").strip()

            if not content:
                return

            incoming = IncomingMessage(
                text=content,
                sender_id=str(message.author.id),
                sender_name=message.author.display_name,
                channel_name=self.name,
                channel_id=str(message.channel.id),
                is_group=message.guild is not None,
                attachments=[a.url for a in message.attachments],
            )

            try:
                response = await self._handler(incoming)
                if response:
                    # Discord has 2000 char limit
                    for chunk in _split_message(response, 2000):
                        await message.reply(chunk)
            except Exception as e:
                logger.error("Discord: handler error — %s", e)
                await message.reply("⚠️ שגיאה בעיבוד ההודעה. נסה שוב.")

        # Run bot in background task
        self._task = asyncio.create_task(self._run_bot())
        logger.info("Discord: channel started")

    async def _run_bot(self):
        try:
            await self._client.start(self.token)
        except Exception as e:
            logger.error("Discord: bot crashed — %s", e)

    async def stop(self) -> None:
        if self._client:
            await self._client.close()
            logger.info("Discord: channel stopped")
        if self._task:
            self._task.cancel()

    async def send(self, message: OutgoingMessage) -> bool:
        if not self._client:
            return False
        try:
            channel = self._client.get_channel(int(message.channel_id))
            if channel:
                for chunk in _split_message(message.text, 2000):
                    await channel.send(chunk)
                return True
        except Exception as e:
            logger.error("Discord: send failed — %s", e)
        return False


def _split_message(text: str, max_len: int = 2000) -> list[str]:
    """Split long messages into chunks respecting Discord's limit."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # Try to split at newline
        split_at = text.rfind("\n", 0, max_len)
        if split_at < max_len // 2:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks
