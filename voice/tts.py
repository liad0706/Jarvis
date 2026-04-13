"""Text-to-speech using ElevenLabs API."""

import asyncio
import logging
import os
import struct

logger = logging.getLogger(__name__)

# ElevenLabs returns raw signed-16-bit-LE mono PCM when output_format=pcm_22050
_ELEVENLABS_PCM_RATE = 22050


class TextToSpeech:
    def __init__(self):
        self._api_key: str = ""
        self._voice_id: str = ""
        self._model_id: str = ""
        self._initialized = False

    def init(self):
        self._api_key = os.getenv("JARVIS_ELEVENLABS_API_KEY", "")
        self._voice_id = os.getenv("JARVIS_ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
        self._model_id = os.getenv("JARVIS_ELEVENLABS_MODEL", "eleven_multilingual_v2")
        if not self._api_key:
            raise ValueError(
                "JARVIS_ELEVENLABS_API_KEY is required. "
                "Get one at https://elevenlabs.io → Profile → API Key"
            )
        self._initialized = True
        logger.info("ElevenLabs TTS ready  (voice=%s, model=%s)", self._voice_id, self._model_id)

    async def speak(self, text: str) -> None:
        """Stream ElevenLabs TTS and play through speakers."""
        if not self._initialized:
            self.init()
        if not text or not text.strip():
            return
        await asyncio.to_thread(self._speak_sync, text)

    # ------------------------------------------------------------------
    def _speak_sync(self, text: str):
        import requests
        import sounddevice as sd
        import numpy as np

        url = (
            f"https://api.elevenlabs.io/v1/text-to-speech/{self._voice_id}"
            f"?output_format=pcm_22050"
        )
        headers = {
            "xi-api-key": self._api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "text": text,
            "model_id": self._model_id,
            "voice_settings": {
                "stability": 0.50,
                "similarity_boost": 0.75,
            },
        }

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30, stream=True)
            resp.raise_for_status()

            # Collect raw PCM bytes (signed 16-bit LE mono)
            pcm_bytes = b"".join(resp.iter_content(chunk_size=4096))
            if not pcm_bytes:
                logger.warning("ElevenLabs returned empty audio")
                return

            # Convert to float32 numpy array for sounddevice
            samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0

            # Prepend ~0.6s silence to wake up Bluetooth devices (AirPods etc.)
            # Append ~0.5s silence so the last syllable isn't cut off
            silence_lead = np.zeros(int(_ELEVENLABS_PCM_RATE * 0.6), dtype=np.float32)
            silence_trail = np.zeros(int(_ELEVENLABS_PCM_RATE * 0.5), dtype=np.float32)
            samples = np.concatenate([silence_lead, samples, silence_trail])

            sd.play(samples, samplerate=_ELEVENLABS_PCM_RATE)
            sd.wait()

        except requests.RequestException as exc:
            logger.warning("ElevenLabs TTS request failed: %s", exc)
        except Exception as exc:
            logger.warning("TTS playback error: %s", exc)

    def stop(self):
        """Stop any currently playing audio."""
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
