"""Speech-to-text using SpeechRecognition with Google (free, supports Hebrew)."""

import asyncio
import logging

logger = logging.getLogger(__name__)


class SpeechToText:
    def __init__(self, language: str = "he-IL"):
        self._language = language
        self._recognizer = None
        self._microphone = None
        self._initialized = False

    def init(self):
        import speech_recognition as sr

        self._recognizer = sr.Recognizer()
        # Calibrate for ambient noise on init
        self._microphone = sr.Microphone()
        with self._microphone as source:
            logger.info("Calibrating microphone for ambient noise...")
            self._recognizer.adjust_for_ambient_noise(source, duration=1)
        self._initialized = True
        logger.info("SpeechRecognition ready (language=%s)", self._language)

    async def listen(self, silence_timeout: float = 2.0, max_duration: float = 15.0) -> str:
        """Listen to microphone and return transcribed text."""
        if not self._initialized:
            self.init()
        return await asyncio.to_thread(self._listen_sync, silence_timeout, max_duration)

    def _listen_sync(self, silence_timeout: float, max_duration: float) -> str:
        import speech_recognition as sr

        try:
            with self._microphone as source:
                logger.info("Listening...")
                audio = self._recognizer.listen(
                    source,
                    timeout=silence_timeout + 3,   # wait up to N sec for speech to start
                    phrase_time_limit=max_duration,  # max recording length
                )

            logger.info("Recognizing...")
            text = self._recognizer.recognize_google(audio, language=self._language)
            logger.info("Heard: %s", text)
            return text

        except sr.WaitTimeoutError:
            logger.debug("No speech detected (timeout)")
            return ""
        except sr.UnknownValueError:
            logger.debug("Could not understand audio")
            return ""
        except sr.RequestError as exc:
            logger.warning("Google Speech Recognition error: %s", exc)
            return ""
