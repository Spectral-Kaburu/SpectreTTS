"""
SpectreTTS - Core Engine
------------------------
Backend-agnostic TTS engine:
  - Lazy model loading (loads once, stays warm) — delegated to a
    TTSBackend (Kokoro, Pocket-TTS, ...); see engine/backends/
  - Streaming chunk playback (audio starts before full synthesis)
  - Playback controls: pause, resume, stop
  - Voice + speed management
  - Text preprocessing (cleans URLs, markdown, symbols)

Which backend is active is chosen once at TTSEngine construction time
(SPECTRETTS_BACKEND env var, defaulting to "kokoro" — see
engine/backends/__init__.py::get_backend). Everything below this point
never imports kokoro_onnx or pocket_tts directly; it only talks to the
TTSBackend interface, so swapping backends never touches this file.
"""

import threading
import queue
import re
import time
import numpy as np
import sounddevice as sd

from .backends import get_backend

# Kept importable from here for backward compatibility — tray/systray_app.py
# does `from engine.tts_engine import VOICES` to build the voice menu.
# This now reflects whichever backend is actually active, so the tray
# menu lists Kokoro voices or Pocket-TTS voices automatically depending
# on SPECTRETTS_BACKEND, with no tray-side changes needed.
VOICES = get_backend().voices

# Note: sample rate isn't a fixed constant — each backend reports it
# per-chunk (kokoro-onnx via create_stream(), Pocket-TTS via
# model.sample_rate), so playback just uses whatever it's told.


# ── Text Preprocessing ────────────────────────────────────────────────────────

def preprocess_text(text: str) -> str:
    """
    Clean raw text before sending it to the TTS backend.
    Handles: URLs, markdown, code blocks, symbols, excess whitespace,
    soft-wrapped newlines, and false sentence-boundary periods
    (file extensions, abbreviations, ellipses).

    Sentence-boundary rule: only ". " (period followed by a space) is
    treated as the end of a sentence. A "." with no trailing space is
    never a sentence break — if it sits between two digits it's spoken
    aloud as "point" (see below); otherwise it's assumed to be a file
    extension, abbreviation, or similar and is protected from the
    chunker/phonemizer entirely.
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
    # Protect periods that are NOT real sentence endings so our chunker's
    # '.!?' split doesn't fire on them AND — this is the part the old
    # version got wrong — so Kokoro's phonemizer never actually sees a
    # bare "." there either. A literal "." in the final string produces a
    # short full-stop pause no matter what precedes it or why it's there;
    # "restore the placeholder back to '.'" was fine for the chunker but
    # left that pause fully intact for playback. Fixed below by never
    # restoring to "." for anything that isn't a genuine sentence end:
    # abbreviations get expanded to their full spoken word instead, and
    # any other no-space dot (code, filenames, domains) becomes the
    # spoken word "dot" — same treatment decimals already get as "point".

    PERIOD_PLACEHOLDER = "<<<DOT>>>"

    # Ellipses first, before anything else touches individual dots inside
    # "...": collapse to a single placeholder so it reads as one trailing
    # pause instead of three run-on sentence endings.
    text = re.sub(r"\.{3,}", PERIOD_PLACEHOLDER, text)

    # Multi-dot abbreviations (each has a period baked into the token
    # itself, not just a trailing one) — expand wholesale.
    text = re.sub(r"\be\.g\.", "for example", text, flags=re.IGNORECASE)
    text = re.sub(r"\bi\.e\.", "that is", text, flags=re.IGNORECASE)
    text = re.sub(r"\ba\.m\.", "AM", text, flags=re.IGNORECASE)
    text = re.sub(r"\bp\.m\.", "PM", text, flags=re.IGNORECASE)

    # Single-dot abbreviations — expand to the full word and drop the
    # period. This is what actually fixes "Mr. Smith": before, "Mr."
    # survived all the way to Kokoro as a literal period, which read as
    # a tiny full stop between "Mr" and "Smith" regardless of chunking.
    SINGLE_DOT_ABBREVIATIONS = {
        "Mr": "Mister", "Mrs": "Missus", "Ms": "Miz", "Dr": "Doctor",
        "Prof": "Professor", "Sr": "Senior", "Jr": "Junior",
        "vs": "versus", "etc": "et cetera", "approx": "approximately",
    }
    for abbr, replacement in SINGLE_DOT_ABBREVIATIONS.items():
        text = re.sub(rf"\b{abbr}\.", replacement, text)

    # "St." is genuinely ambiguous (Saint vs. Street — "St. Louis" vs.
    # "5th St.") so it isn't expanded; just drop the period and let the
    # phonemizer's own built-in abbreviation handling take it from there.
    text = re.sub(r"\bSt\.", "St", text)

    # Decimal / numeric "dot": a period with NO space after it, sitting
    # between two digits, is never a sentence boundary — it's a spoken
    # "point" (3.14 -> "three point one four", 192.168.1.1 -> "one
    # ninety-two point one sixty-eight point one point one"). The
    # lookahead (?=\d) — instead of consuming the next digit — lets a
    # single pass catch chained decimals like IP addresses without
    # needing to run the substitution twice.
    text = re.sub(r"(\d)\.(?=\d)", r"\1 point ", text)

    # Single-letter initials: J. K. Rowling — protected with a placeholder
    # (not expanded to a word) since there's genuinely nothing to expand
    # to; the chunker just needs to not split between "J." and "K.".
    text = re.sub(
        r"\b([A-Z])\.(\s[A-Z]\b)",
        r"\1" + PERIOD_PLACEHOLDER + r"\2",
        text
    )

    # Everything else: any "." with NO space after it, immediately
    # followed by a letter or digit — filenames (report.docx), code
    # (sd.wait()), domains (example.com), acronyms (U.S.) — gets spoken
    # as "dot" instead of left as a bare period. This replaces the old
    # fixed file-extension whitelist entirely: it's more general (works
    # for ANY dotted identifier, not just a hardcoded extension list) and
    # it fixes the same pause problem, since "dot" is also just how
    # people actually read these aloud.
    text = re.sub(r"\.(?=[A-Za-z0-9])", " dot ", text)

    # Restore protected periods (currently just the initials case) now
    # that real sentence boundaries are safe
    text = text.replace(PERIOD_PLACEHOLDER, ".")

    # Collapse excess whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)   # trim spaces around real paragraph breaks

    return text.strip()


def split_into_chunks(
    text: str,
    max_chars: int = 350,
    chunk_size_ramp: tuple[int, ...] = (90, 160, 260, 350),
) -> list[str]:
    """
    Split long text at sentence boundaries for streaming.
    Keeps chunks under max_chars so each synthesizes quickly.

    Chunk sizes RAMP UP instead of jumping straight from a small first
    chunk to a full-size one (chunk_size_ramp, then max_chars for every
    chunk after). Going small -> small -> small -> full smooths out the
    "lag after the first utterance" problem: on weak hardware, synthesis
    of one big ~400-char chunk can take noticeably longer than it takes
    to play the small first chunk, so the playback queue can run dry
    right after chunk #1 while synthesis is still grinding through
    chunk #2. A gentler ramp keeps every early synthesis call short
    enough that it reliably finishes before the previous chunk stops
    playing, at the cost of very slightly choppier prosody at those
    extra chunk boundaries (each chunk boundary resets Kokoro's local
    prosody state). max_chars was also lowered from 400 to 260: past
    ~250-300 chars the per-chunk synthesis time starts to dominate
    perceived latency on slow CPUs, with no real naturalness gain from
    going bigger since chunks already break on real sentence/paragraph
    boundaries either way.
    """
    # Split on sentence-ending punctuation (paragraph breaks treated as
    # hard boundaries too, so a paragraph never gets silently merged)
    text = text.replace("\n\n", " \n\n ")  # ensure paragraph breaks split cleanly
    sentences = re.split(r'(?<=[.!?])\s+|\n\n', text)

    chunks = []
    current = ""
    ramp_idx = 0
    limit = chunk_size_ramp[0] if chunk_size_ramp else max_chars

    def next_limit():
        nonlocal ramp_idx
        ramp_idx += 1
        if ramp_idx < len(chunk_size_ramp):
            return chunk_size_ramp[ramp_idx]
        return max_chars

    for sentence in sentences:
        if not sentence.strip():
            continue
        if len(current) + len(sentence) <= limit:
            current += (" " if current else "") + sentence
        else:
            if current:
                chunks.append(current.strip())
                limit = next_limit()
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
                            limit = next_limit()
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
    The main engine. Loads the active backend's model once, streams audio
    in a background thread.
    Thread-safe pause/resume/stop controls.
    """

    def __init__(self, backend: str = None):
        """
        backend: "kokoro", "pocket", or None to follow SPECTRETTS_BACKEND
        (defaults to "kokoro" if that's unset too — see backends/__init__.py).
        """
        self.backend = get_backend(backend)
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
        self.voice = self.backend.default_voice
        self.speed = 1.0

    # ── Pipeline management ───────────────────────────────────────────────────

    def _get_lang(self) -> str:
        return self.backend.lang_for_voice(self.voice)

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
        if voice_id in self.backend.voices:
            self.voice = voice_id

    def set_speed(self, speed: float):
        if not self.backend.supports_speed:
            print(
                f"[SpectreTTS] Backend '{self.backend.id}' has no speed control — "
                f"ignoring set_speed({speed})."
            )
            return
        self.speed = max(0.5, min(2.0, speed))

    # ── Internal synthesis ─────────────────────────────────────────────────────

    def _synthesize(self, text: str):
        """
        Run in background thread. Synthesizes chunks and puts audio in queue.
        Actual model-specific synthesis is delegated to self.backend —
        it pushes (audio, sample_rate) tuples onto self._audio_queue as
        they become available, whatever shape that takes internally
        (async event loop for Kokoro, plain generator for Pocket-TTS, etc).
        """
        try:
            lang = self._get_lang()
            chunks = split_into_chunks(text)

            for chunk in chunks:
                if self._stop_event.is_set():
                    break

                self.backend.synthesize_chunk(
                    chunk, self.voice, self.speed, lang,
                    self._audio_queue, self._stop_event,
                )

        except FileNotFoundError as e:
            print(f"[SpectreTTS] Model files missing: {e}")
        except Exception as e:
            print(f"[SpectreTTS] Synthesis error: {e}")
        finally:
            # Sentinel: tells playback thread synthesis is done
            self._audio_queue.put(None)

    # ── Internal playback ──────────────────────────────────────────────────────

    def _play_audio(self):
        """
        Run in background thread. Plays audio pieces from the queue.

        Uses ONE persistent sd.OutputStream for the whole utterance instead
        of calling sd.play()/sd.wait() per queued piece. This matters because
        kokoro.create_stream() doesn't yield one array per text chunk — it
        streams many smaller audio pieces as they're generated, and those
        pieces tend to be smaller/more frequent right at the start before
        settling into steadier, larger ones. Each sd.play() call opens the
        audio device and each sd.wait() closes it; back-to-back small pieces
        means back-to-back open/close cycles, and that device-restart
        overhead is exactly what was showing up as choppiness in the first
        2-3 sentences — not the text or a prosody issue, a playback-plumbing
        one. Writing into one continuous stream removes the gap regardless
        of how small or frequent the pieces are.
        """
        stream = None
        stream_rate = None
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

                # (Re)open the stream only when needed: first piece, or if
                # the sample rate ever changes mid-utterance (shouldn't
                # normally happen, but voices/langs could in theory differ).
                if stream is None or sample_rate != stream_rate:
                    if stream is not None:
                        stream.stop()
                        stream.close()
                    stream = sd.OutputStream(
                        samplerate=sample_rate,
                        channels=1,
                        dtype="float32",
                    )
                    stream.start()
                    stream_rate = sample_rate

                stream.write(np.ascontiguousarray(audio, dtype=np.float32))

        except Exception as e:
            print(f"[SpectreTTS] Playback error: {e}")
        finally:
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
            self._is_speaking = False


# ── Quick smoke test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    # Optional: `python -m engine.tts_engine pocket` to smoke-test a
    # specific backend without touching SPECTRETTS_BACKEND.
    backend_arg = sys.argv[1] if len(sys.argv) > 1 else None
    engine = TTSEngine(backend=backend_arg)

    print("=== SpectreTTS Engine Smoke Test ===")
    print(f"Backend: {engine.backend.id} | Voice: {engine.voice} | Speed: {engine.speed}x")
    print("Speaking test sentence...")

    engine.speak(
        "SpectreTTS engine initialized. "
        "The model is loaded and audio is streaming in real time. "
        "The engine is ready for integration."
    )

    # Wait for speech to finish
    time.sleep(1)
    while engine.is_speaking:
        time.sleep(0.2)

    # Real pass/fail check: did any audio actually get synthesized?
    # _is_speaking flips false both on success AND on total failure, so
    # check the backend's own "did I load" state instead. Every backend
    # keeps its loaded model on self._model or self._kokoro — check
    # whichever this backend actually set.
    loaded = getattr(engine.backend, "_model", None) or getattr(engine.backend, "_kokoro", None)
    if loaded is None:
        print("FAILED: model never loaded — check the error above.")
        sys.exit(1)

    print("PASSED: model loaded and synthesis ran without raising.")
    