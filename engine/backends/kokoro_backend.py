"""
Kokoro-ONNX backend. This is the original SpectreTTS engine, unmoved
in behavior — just relocated behind the TTSBackend interface so it can
sit side by side with other backends instead of being the only option.
"""

import asyncio
import os
import queue
import threading

from .base import TTSBackend

# ── Available voices ──────────────────────────────────────────────────────────
VOICES = {
    # American English (Female)
    "af_heart":   ("Heart",   "en-us", "female"),
    "af_bella":   ("Bella",   "en-us", "female"),
    "af_nicole":  ("Nicole",  "en-us", "female"),
    "af_sarah":   ("Sarah",   "en-us", "female"),
    "af_sky":     ("Sky",     "en-us", "female"),
    # American English (Male)
    "am_adam":    ("Adam",    "en-us", "male"),
    "am_michael": ("Michael", "en-us", "male"),
    # British English (Female)
    "bf_emma":    ("Emma",    "en-gb", "female"),
    "bf_isabella":("Isabella","en-gb", "female"),
    # British English (Male)
    "bm_george":  ("George",  "en-gb", "male"),
    "bm_lewis":   ("Lewis",   "en-gb", "male"),
}

LANG_CODE_MAP = {
    "en-us": "en-us",
    "en-gb": "en-gb",
    "es":    "es",
    "fr":    "fr-fr",
    "hi":    "hi",
    "it":    "it",
    "pt-br": "pt-br",
}

# kokoro-onnx loads weights from local files rather than auto-downloading
# from Hugging Face on first use. Download once:
#   wget https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
#   wget https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
MODEL_DIR = os.environ.get(
    "SPECTRETTS_KOKORO_MODEL_DIR",
    os.environ.get(
        "SPECTRETTS_MODEL_DIR",  # legacy name, kept working for existing installs
        os.path.join(os.path.expanduser("~"), ".cache", "spectretts", "models"),
    ),
)
MODEL_PATH = os.path.join(MODEL_DIR, "kokoro-v1.0.onnx")
VOICES_PATH = os.path.join(MODEL_DIR, "voices-v1.0.bin")


class KokoroBackend(TTSBackend):
    id = "kokoro"
    voices = VOICES
    default_voice = "af_heart"
    supports_speed = True

    def __init__(self):
        self._kokoro = None
        self._lock = threading.Lock()

    def load(self):
        if self._kokoro is None:
            with self._lock:
                if self._kokoro is None:  # re-check inside lock
                    from kokoro_onnx import Kokoro

                    if not os.path.exists(MODEL_PATH) or not os.path.exists(VOICES_PATH):
                        raise FileNotFoundError(
                            f"Kokoro ONNX model files not found.\n"
                            f"Expected:\n  {MODEL_PATH}\n  {VOICES_PATH}\n"
                            f"Download them from:\n"
                            f"  https://github.com/thewh1teagle/kokoro-onnx/releases\n"
                            f"into {MODEL_DIR}"
                        )
                    print(f"[SpectreTTS] Loading Kokoro ONNX model from {MODEL_DIR}...")
                    self._kokoro = Kokoro(MODEL_PATH, VOICES_PATH)
                    print("[SpectreTTS] Model ready.")
        return self._kokoro

    def lang_for_voice(self, voice_id: str) -> str:
        info = self.voices.get(voice_id)
        if not info:
            return "en-us"
        return LANG_CODE_MAP.get(info[1], "en-us")

    def synthesize_chunk(self, chunk, voice, speed, lang, audio_queue, stop_event):
        kokoro = self.load()
        # kokoro-onnx's create_stream() is async, so we spin up a small
        # event loop just for this chunk to drive it. The rest of the
        # engine (threading, queue, playback) never touches asyncio.
        asyncio.run(
            self._stream_chunk(kokoro, chunk, voice, speed, lang, audio_queue, stop_event)
        )

    async def _stream_chunk(self, kokoro, chunk, voice, speed, lang, audio_queue, stop_event):
        async for audio, sample_rate in kokoro.create_stream(
            chunk, voice=voice, speed=speed, lang=lang
        ):
            if stop_event.is_set():
                break
            if audio is not None and len(audio) > 0:
                audio_queue.put((audio, sample_rate))
                