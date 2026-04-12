from core.skill_base import BaseSkill
import os
from pydub import AudioSegment
from faster_whisper import WhisperModel

class VoiceInputSkill(BaseSkill):
    name = "voice_input"
    description = "Record and transcribe speech from the default microphone using a local engine."
    REQUIREMENTS: list[str] = ["pydub", "faster-whisper"]

    def __init__(self):
        self.settings = None
        self.recording_file = os.path.join("data", "latest_recording.wav")
        self.model = WhisperModel("base", device="cpu")

    async def do_start_recording(self):
        """Start recording from the default microphone."""
        import sounddevice as sd
        import numpy as np

        print("Recording... Press 'q' to stop.")
        frames = []
        stream = sd.InputStream(callback=lambda indata, frames, time, status: frames.append(indata))
        with stream:
            while True:
                key = input()
                if key == "q":
                    break
        audio_data = np.concatenate(frames, axis=0).flatten().astype(np.int16)
        recording = AudioSegment(audio_data, frame_rate=44100, sample_width=2, channels=1)
        recording.export(self.recording_file, format="wav")

    async def do_stop_recording(self):
        """Stop the current recording."""
        print("Recording stopped.")
        self.recording_file = os.path.join("data", "latest_recording.wav")

    async def do_transcribe_latest_clip(self) -> dict:
        """
        Transcribe the latest recorded clip.
        """
        try:
            segments, info = self.model.transcribe(self.recording_file)
            text = "".join(seg.text for seg in segments)
            return {"status": "ok", "transcription": text, "language": info.language}
        except Exception as e:
            return {"error": str(e)}