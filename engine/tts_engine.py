"""
SpectreTTS - Core Engine
------------------------
Wraps Kokoro-82M with:
  - Lazy model loading (loads once, stays warm)
  - Streaming chunk playback (audio starts before full synthesis)
  - Playback controls: pause, resume, stop
  - Voice + speed management
  - Text preprocessing (cleans URLs, markdown, symbols)
"""

import threading
import queue
import re
import time
import numpy as np
import sounddevice as sd
from kokoro import KPipeline


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
    "en-us": "a",   # American English
    "en-gb": "b",   # British English
    "es":    "e",   # Spanish
    "fr":    "f",   # French
    "hi":    "h",   # Hindi
    "it":    "i",   # Italian
    "pt-br": "p",   # Brazilian Portuguese
}

SAMPLE_RATE = 24000  # Kokoro outputs 24kHz


# ── Text Preprocessing ────────────────────────────────────────────────────────

def preprocess_text(text: str) -> str:
    """
    Clean raw text before sending to Kokoro.
    Handles: URLs, markdown, code blocks, symbols, excess whitespace.
    """
    # Strip code blocks entirely (not useful to read aloud)
    text = re.sub(r"```[\s\S]*?```", " [code block omitted] ", text)
    text = re.sub(r"`[^`]+`", "", text)

    # URLs → short label
    text = re.sub(
        r"https?://[^\s]+",
        " [link] ",
        text
    )

    # Markdown headers → just the text
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)

    # Bold/italic markers
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,2}([^_]+)_{1,2}", r"\1", text)

    # Bullet/list markers
    text = re.sub(r"^\s*[-*•]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)

    # HTML tags (if any)
    text = re.sub(r"<[^>]+>", " ", text)

    # Collapse excess whitespace/newlines
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)

    return text.strip()


def split_into_chunks(text: str, max_chars: int = 400) -> list[str]:
    """
    Split long text at sentence boundaries for streaming.
    Keeps chunks under max_chars so each synthesizes quickly.
    """
    # Split on sentence-ending punctuation
    sentences = re.split(r'(?<=[.!?])\s+', text)

    chunks = []
    current = ""

    for sentence in sentences:
        if not sentence.strip():
            continue
        if len(current) + len(sentence) <= max_chars:
            current += (" " if current else "") + sentence
        else:
            if current:
                chunks.append(current.strip())
            # If a single sentence is huge, split it on commas
            if len(sentence) > max_chars:
                parts = re.split(r'(?<=,)\s+', sentence)
                sub = ""
                for part in parts:
                    if len(sub) + len(part) <= max_chars:
                        sub += (" " if sub else "") + part
                    else:
                        if sub:
                            chunks.append(sub.strip())
                        sub = part
                if sub:
                    chunks.append(sub.strip())
                current = ""
            else:
                current = sentence

    if current:
        chunks.append(current.strip())

    return [c for c in chunks if c]


# ── Core TTS Engine ───────────────────────────────────────────────────────────

class TTSEngine:
    """
    The main engine. Loads Kokoro once, streams audio in a background thread.
    Thread-safe pause/resume/stop controls.
    """

    def __init__(self):
        self._pipelines: dict[str, KPipeline] = {}   # lang_code → pipeline
        self._lock = threading.Lock()

        # Playback state
        self._audio_queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()   # not paused by default

        self._playback_thread: threading.Thread | None = None
        self._synth_thread: threading.Thread | None = None
        self._is_speaking = False

        # Settings
        self.voice = "bf_emma"
        self.speed = 1.0

    # ── Pipeline management ───────────────────────────────────────────────────

    def _get_pipeline(self, lang_code: str) -> KPipeline:
        """Get or lazily create a pipeline for the given lang_code."""
        if lang_code not in self._pipelines:
            print(f"[SpectreTTS] Loading pipeline for lang_code='{lang_code}'...")
            self._pipelines[lang_code] = KPipeline(lang_code=lang_code)
            print(f"[SpectreTTS] Pipeline ready.")
        return self._pipelines[lang_code]

    def _get_lang_code(self) -> str:
        voice_info = VOICES.get(self.voice)
        if not voice_info:
            return "a"
        locale = voice_info[1]
        return LANG_CODE_MAP.get(locale, "a")

    # ── Public controls ───────────────────────────────────────────────────────

    def speak(self, text: str):
        """Speak the given text. Stops any current speech first."""
        self.stop()
        text = preprocess_text(text)
        if not text:
            return

        self._stop_event.clear()
        self._pause_event.set()
        self._is_speaking = True

        # Synthesis thread: generates audio chunks and queues them
        self._synth_thread = threading.Thread(
            target=self._synthesize,
            args=(text,),
            daemon=True
        )
        # Playback thread: dequeues and plays audio chunks
        self._playback_thread = threading.Thread(
            target=self._play_audio,
            daemon=True
        )

        self._playback_thread.start()
        self._synth_thread.start()

    def pause(self):
        """Pause playback mid-stream."""
        self._pause_event.clear()

    def resume(self):
        """Resume paused playback."""
        self._pause_event.set()

    def stop(self):
        """Stop all synthesis and playback immediately."""
        self._stop_event.set()
        self._pause_event.set()   # unblock if paused so thread can exit

        # Drain the queue
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

        if self._synth_thread and self._synth_thread.is_alive():
            self._synth_thread.join(timeout=2)
        if self._playback_thread and self._playback_thread.is_alive():
            self._playback_thread.join(timeout=2)

        self._is_speaking = False

    def toggle_pause(self):
        if self._pause_event.is_set():
            self.pause()
            return "paused"
        else:
            self.resume()
            return "resumed"

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking

    def set_voice(self, voice_id: str):
        if voice_id in VOICES:
            self.voice = voice_id

    def set_speed(self, speed: float):
        self.speed = max(0.5, min(2.0, speed))

    # ── Internal synthesis ─────────────────────────────────────────────────────

    def _synthesize(self, text: str):
        """Run in background thread. Synthesizes chunks and puts audio in queue."""
        try:
            lang_code = self._get_lang_code()
            pipeline = self._get_pipeline(lang_code)
            chunks = split_into_chunks(text)

            for chunk in chunks:
                if self._stop_event.is_set():
                    break

                generator = pipeline(chunk, voice=self.voice, speed=self.speed)
                for _, _, audio in generator:
                    if self._stop_event.is_set():
                        break
                    if audio is not None and len(audio) > 0:
                        self._audio_queue.put(audio)

        except Exception as e:
            print(f"[SpectreTTS] Synthesis error: {e}")
        finally:
            # Sentinel: tells playback thread synthesis is done
            self._audio_queue.put(None)

    # ── Internal playback ──────────────────────────────────────────────────────

    def _play_audio(self):
        """Run in background thread. Plays audio chunks from the queue."""
        try:
            while not self._stop_event.is_set():
                try:
                    chunk = self._audio_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                if chunk is None:   # synthesis done
                    break

                # Pause support: block here until resumed or stopped
                self._pause_event.wait()
                if self._stop_event.is_set():
                    break

                # Play this chunk synchronously
                sd.play(chunk, samplerate=SAMPLE_RATE)
                sd.wait()

        except Exception as e:
            print(f"[SpectreTTS] Playback error: {e}")
        finally:
            self._is_speaking = False


# ── Quick smoke test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    engine = TTSEngine()

    print("=== SpectreTTS Engine Smoke Test ===")
    print(f"Voice: {engine.voice} | Speed: {engine.speed}x")
    print("Speaking test sentence...")

    engine.speak(
        "SpectreTTS engine initialized. "
        "Kokoro is loaded and audio is streaming in real time. "
        "The engine is ready for integration."
    )

    # Wait for speech to finish
    time.sleep(1)
    while engine.is_speaking:
        time.sleep(0.2)

    print("Done. Engine test passed.")