"""Telegram channel — connects Jarvis to a Telegram bot.

Requires: pip install python-telegram-bot
Set JARVIS_TELEGRAM_TOKEN in .env to your bot token (from @BotFather).
Optionally set JARVIS_TELEGRAM_ALLOWED_USERS (comma-separated user IDs or usernames).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from channels.base import BaseChannel, IncomingMessage, OutgoingMessage, MessageHandler

logger = logging.getLogger(__name__)


class TelegramChannel(BaseChannel):
    name = "telegram"

    def __init__(self, token: str = "", allowed_users: str = ""):
        self.token = token
        self.allowed_users: set[str] = {
            u.strip().lower().lstrip("@")
            for u in allowed_users.split(",") if u.strip()
        }
        self._app: Any = None
        self._handler: MessageHandler | None = None
        self._task: asyncio.Task | None = None
        self.enabled = bool(token)

    async def start(self, handler: MessageHandler) -> None:
        if not self.token:
            logger.warning("Telegram: no token configured, skipping")
            return

        try:
            from telegram import Update
            from telegram.ext import (
                ApplicationBuilder,
                MessageHandler as TGHandler,
                ContextTypes,
                filters,
            )
        except ImportError:
            logger.error("Telegram: python-telegram-bot not installed. Run: pip install python-telegram-bot")
            return

        self._handler = handler

        self._app = ApplicationBuilder().token(self.token).build()

        async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if not update.message or not update.message.text:
                return

            user = update.message.from_user
            if not user:
                return

            print(f"[Telegram] Message from {user.id} ({user.username}): {update.message.text[:80]}")

            # Filter by allowed users
            if self.allowed_users:
                user_id_str = str(user.id).lower()
                username = (user.username or "").lower()
                if user_id_str not in self.allowed_users and username not in self.allowed_users:
                    await update.message.reply_text("⛔ אין לך הרשאה להשתמש ב-Jarvis.")
                    return

            incoming = IncomingMessage(
                text=update.message.text,
                sender_id=str(user.id),
                sender_name=user.first_name or user.username or "",
                channel_name=self.name,
                channel_id=str(update.message.chat_id),
                is_group=update.message.chat.type in ("group", "supergroup"),
            )

            # In groups, only respond when mentioned or replied to
            if incoming.is_group:
                bot_username = context.bot.username or ""
                text_lower = incoming.text.lower()
                is_reply_to_bot = (
                    update.message.reply_to_message
                    and update.message.reply_to_message.from_user
                    and update.message.reply_to_message.from_user.id == context.bot.id
                )
                if not is_reply_to_bot and f"@{bot_username.lower()}" not in text_lower and not text_lower.startswith("jarvis"):
                    return
                # Clean up mention
                incoming.text = incoming.text.replace(f"@{bot_username}", "").strip()

            try:
                # Show typing indicator
                await update.message.chat.send_action("typing")
                response = await self._handler(incoming)
                if response:
                    # Telegram has 4096 char limit
                    for chunk in _split_message(response, 4096):
                        await update.message.reply_text(
                            chunk,
                            parse_mode="Markdown" if _looks_like_markdown(chunk) else None,
                        )
            except Exception as e:
                logger.error("Telegram: handler error — %s", e)
                await update.message.reply_text("⚠️ שגיאה בעיבוד ההודעה. נסה שוב.")

        self._app.add_handler(TGHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        # Start polling in background
        self._task = asyncio.create_task(self._run_polling())
        logger.info("Telegram: channel started")
        print("[Telegram] Bot started — polling for messages...")

    async def _run_polling(self):
        try:
            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling(drop_pending_updates=True)
            print("[Telegram] Polling active — ready to receive messages")
            # Keep running
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Telegram: polling crashed — %s", e)
            print(f"[Telegram] POLLING CRASHED: {e}")

    async def stop(self) -> None:
        if self._app:
            try:
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except Exception:
                pass
            logger.info("Telegram: channel stopped")
        if self._task:
            self._task.cancel()

    async def send(self, message: OutgoingMessage) -> bool:
        if not self._app:
            return False
        try:
            chat_id = message.recipient_id or message.channel_id
            if chat_id:
                for chunk in _split_message(message.text, 4096):
                    await self._app.bot.send_message(
                        chat_id=int(chat_id),
                        text=chunk,
                        parse_mode="Markdown" if _looks_like_markdown(chunk) else None,
                    )
                return True
        except Exception as e:
            logger.error("Telegram: send failed — %s", e)
        return False


def _split_message(text: str, max_len: int = 4096) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at < max_len // 2:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


def _looks_like_markdown(text: str) -> bool:
    """Simple heuristic: does the text contain markdown formatting?"""
    markers = ["**", "__", "```", "`", "- ", "* ", "# "]
    return any(m in text for m in markers)
