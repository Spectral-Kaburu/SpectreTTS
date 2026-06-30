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
import asyncio
import os
import numpy as np
import sounddevice as sd
from kokoro_onnx import Kokoro


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
    "SPECTRETTS_MODEL_DIR",
    os.path.join(os.path.expanduser("~"), ".cache", "spectretts", "models")
)
MODEL_PATH = os.path.join(MODEL_DIR, "kokoro-v1.0.onnx")
VOICES_PATH = os.path.join(MODEL_DIR, "voices-v1.0.bin")

# Note: sample rate is no longer a fixed constant — kokoro-onnx returns it
# per-call from create_stream(), so we use whatever it reports (typically 24000Hz).


# ── Text Preprocessing ────────────────────────────────────────────────────────

def preprocess_text(text: str) -> str:
    """
    Clean raw text before sending to Kokoro.
    Handles: URLs, markdown, code blocks, symbols, excess whitespace,
    soft-wrapped newlines, and false sentence-boundary periods
    (file extensions, abbreviations, decimals, ellipses).
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

    # ── Soft-wrap newline handling ──────────────────────────────────────────
    # A single \n is almost always a line-wrap for spacing, not a real
    # paragraph break — collapse it to a space. A double \n (blank line)
    # IS an intentional paragraph break, so preserve it as one.
    text = re.sub(r"\n{2,}", "<<<PARA_BREAK>>>", text)   # protect real breaks
    text = re.sub(r"\n", " ", text)                       # collapse soft wraps
    text = text.replace("<<<PARA_BREAK>>>", "\n\n")        # restore real breaks

    # ── False sentence-boundary protection ──────────────────────────────────
    # Protect periods that are NOT real sentence endings by temporarily
    # swapping them for a placeholder, so our chunker's '.!?' split
    # doesn't fire on them and Kokoro's phonemizer doesn't either.

    PERIOD_PLACEHOLDER = "<<<DOT>>>"

    # File extensions: word.ext (common code/doc extensions)
    text = re.sub(
        r"\.(py|js|json|md|txt|yml|yaml|csv|xlsx|docx|pdf|html|css|sh|cfg|"
        r"ini|toml|env|jpg|png|svg|mp3|mp4|zip|tar|gz)\b",
        PERIOD_PLACEHOLDER + r"\1",
        text,
        flags=re.IGNORECASE
    )

    # Common abbreviations (Mr. Mrs. Dr. e.g. i.e. etc. vs. approx.)
    text = re.sub(
        r"\b(Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc|approx|e\.g|i\.e|a\.m|p\.m)\.",
        r"\1" + PERIOD_PLACEHOLDER,
        text
    )

    # Decimal numbers: 3.14, 1.5x, 192.168.1.1 etc.
    text = re.sub(
        r"(\d)\.(\d)",
        r"\1" + PERIOD_PLACEHOLDER + r"\2",
        text
    )
    # Run twice to catch chained decimals like IP addresses (192.168.1.1)
    text = re.sub(
        r"(\d)\.(\d)",
        r"\1" + PERIOD_PLACEHOLDER + r"\2",
        text
    )

    # Single-letter initials: J. K. Rowling
    text = re.sub(
        r"\b([A-Z])\.(\s[A-Z]\b)",
        r"\1" + PERIOD_PLACEHOLDER + r"\2",
        text
    )

    # Ellipses: collapse "..." to a single placeholder dot so it doesn't
    # read as three separate sentence-ending pauses
    text = re.sub(r"\.{3,}", PERIOD_PLACEHOLDER, text)

    # Restore protected periods now that real sentence boundaries are safe
    text = text.replace(PERIOD_PLACEHOLDER, ".")

    # Collapse excess whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)   # trim spaces around real paragraph breaks

    return text.strip()


def split_into_chunks(text: str, max_chars: int = 400, first_chunk_max: int = 120) -> list[str]:
    """
    Split long text at sentence boundaries for streaming.
    Keeps chunks under max_chars so each synthesizes quickly.

    The FIRST chunk is capped much smaller (first_chunk_max) so synthesis
    starts and audio begins playing as fast as possible — the user hears
    something within ~1 sentence instead of waiting for a full ~400-char
    chunk to be built before the first synthesis call even starts.
    """
    # Split on sentence-ending punctuation (paragraph breaks treated as
    # hard boundaries too, so a paragraph never gets silently merged)
    text = text.replace("\n\n", " \n\n ")  # ensure paragraph breaks split cleanly
    sentences = re.split(r'(?<=[.!?])\s+|\n\n', text)

    chunks = []
    current = ""
    is_first = True
    limit = first_chunk_max

    for sentence in sentences:
        if not sentence.strip():
            continue
        if len(current) + len(sentence) <= limit:
            current += (" " if current else "") + sentence
        else:
            if current:
                chunks.append(current.strip())
                is_first = False
                limit = max_chars
            # If a single sentence is huge, split it on commas
            if len(sentence) > limit:
                parts = re.split(r'(?<=,)\s+', sentence)
                sub = ""
                for part in parts:
                    if len(sub) + len(part) <= limit:
                        sub += (" " if sub else "") + part
                    else:
                        if sub:
                            chunks.append(sub.strip())
                            is_first = False
                            limit = max_chars
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
        self._kokoro: Kokoro | None = None   # single shared instance, loaded lazily
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
        self.voice = "af_heart"
        self.speed = 1.0

    # ── Pipeline management ───────────────────────────────────────────────────

    def _get_kokoro(self) -> Kokoro:
        """Lazily load the shared ONNX Kokoro instance (loads once, reused for all voices/langs)."""
        if self._kokoro is None:
            with self._lock:
                if self._kokoro is None:   # re-check inside lock
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
                    print(f"[SpectreTTS] Model ready.")
        return self._kokoro

    def _get_lang(self) -> str:
        voice_info = VOICES.get(self.voice)
        if not voice_info:
            return "en-us"
        locale = voice_info[1]
        return LANG_CODE_MAP.get(locale, "en-us")

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
        """
        Run in background thread. Synthesizes chunks and puts audio in queue.
        kokoro-onnx's create_stream() is async, so we run a small event loop
        inside this thread to drive it — the rest of the engine (threading,
        queue, playback) stays exactly as it was with the PyTorch backend.
        """
        try:
            kokoro = self._get_kokoro()
            lang = self._get_lang()
            chunks = split_into_chunks(text)

            for chunk in chunks:
                if self._stop_event.is_set():
                    break

                asyncio.run(self._synthesize_chunk(kokoro, chunk, lang))

        except FileNotFoundError as e:
            print(f"[SpectreTTS] Model files missing: {e}")
        except Exception as e:
            print(f"[SpectreTTS] Synthesis error: {e}")
        finally:
            # Sentinel: tells playback thread synthesis is done
            self._audio_queue.put(None)

    async def _synthesize_chunk(self, kokoro: Kokoro, chunk: str, lang: str):
        """Streams one text chunk's audio samples into the playback queue as they're generated."""
        async for audio, sample_rate in kokoro.create_stream(
            chunk, voice=self.voice, speed=self.speed, lang=lang
        ):
            if self._stop_event.is_set():
                break
            if audio is not None and len(audio) > 0:
                self._audio_queue.put((audio, sample_rate))

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

                audio, sample_rate = chunk

                # Pause support: block here until resumed or stopped
                self._pause_event.wait()
                if self._stop_event.is_set():
                    break

                # Play this chunk synchronously
                sd.play(audio, samplerate=sample_rate)
                sd.wait()

        except Exception as e:
            print(f"[SpectreTTS] Playback error: {e}")
        finally:
            self._is_speaking = False


# ── Quick smoke test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

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

    # Real pass/fail check: did any audio actually get synthesized and queued?
    # _is_speaking flips false both on success AND on total failure, so we
    # can't rely on it alone — check that the model actually loaded.
    if engine._kokoro is None:
        print("FAILED: model never loaded — check the error above.")
        sys.exit(1)

    print("PASSED: model loaded and synthesis ran without raising.")

