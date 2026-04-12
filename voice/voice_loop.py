"""Continuous voice loop: listen → (wake word) → transcribe → orchestrate → speak → repeat."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class VoiceLoop:
    """Persistent listen-respond cycle that integrates STT, TTS, and the orchestrator.

    Usage:
        loop = VoiceLoop(stt=stt, tts=tts, orchestrator=orchestrator)
        asyncio.create_task(loop.run_forever())
    """

    def __init__(
        self,
        stt,
        tts,
        orchestrator,
        wake_word: str = "jarvis",
        session_id: str = "voice",
    ):
        self.stt = stt
        self.tts = tts
        self.orchestrator = orchestrator
        self.wake_word = wake_word.lower()
        self.session_id = session_id
        self._running = False

    def stop(self):
        """Signal the loop to stop after the current iteration."""
        self._running = False

    async def run_forever(self):
        """Main loop: wait for speech → check for wake word → process → speak."""
        self._running = True
        logger.info("VoiceLoop started (wake_word=%r)", self.wake_word)

        while self._running:
            try:
                # Wait for speech
                text = await self.stt.listen()
                if not text:
                    continue

                text_lower = text.lower()

                # Require wake word if configured
                if self.wake_word and self.wake_word not in text_lower:
                    continue

                # Strip wake word from query
                query = text_lower.replace(self.wake_word, "").strip()

                # If nothing after wake word, ask and listen again
                if not query:
                    await self.tts.speak("כן?")
                    query = await self.stt.listen()
                    if not query:
                        continue

                logger.info("VoiceLoop query: %r", query)

                # Process through orchestrator
                response = await self.orchestrator.handle(
                    query,
                    session_id=self.session_id,
                    channel="voice",
                )

                # Speak the response
                if response:
                    await self.tts.speak(response)

            except asyncio.CancelledError:
                logger.info("VoiceLoop cancelled")
                break
            except Exception as e:
                logger.warning("VoiceLoop error: %s", e)
                await asyncio.sleep(1)

        self._running = False
        logger.info("VoiceLoop stopped")
