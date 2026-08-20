"""
Backend interface for SpectreTTS synthesis engines.

Each backend owns model loading, voice resolution, and per-chunk
synthesis. Everything else in tts_engine.py — threading, chunking,
text preprocessing, pause/resume/stop, the persistent sd.OutputStream —
is backend-agnostic and stays exactly as it was, regardless of which
backend is active.

To add a new backend: subclass TTSBackend, implement load() and
synthesize_chunk(), fill in `voices`/`default_voice`, and register it
in backends/__init__.py's _REGISTRY.
"""

from abc import ABC, abstractmethod
import queue
import threading


class TTSBackend(ABC):
    """One TTS engine implementation (Kokoro, Pocket-TTS, ...)."""

    #: short id used by SPECTRETTS_BACKEND and the tray/status output
    id: str = "base"

    #: {voice_id: (display_name, locale, gender)} — same shape VOICES
    #: has always had, so callers (tray menu, _get_lang()) don't need
    #: to know which backend is actually active.
    voices: dict = {}

    #: sensible default voice_id for this backend
    default_voice: str = ""

    #: whether set_speed() does anything for this backend. Kokoro has
    #: a real speed= parameter; not every backend does (see Pocket).
    #: TTSEngine.set_speed() checks this and no-ops with a log line
    #: instead of silently pretending speed changed.
    supports_speed: bool = True

    @abstractmethod
    def load(self):
        """
        Load model weights and return the loaded model/session object.
        Called lazily on first use, and should be idempotent/cheap on
        every call after the first (cache the loaded model on self).
        """
        raise NotImplementedError

    @abstractmethod
    def synthesize_chunk(
        self,
        chunk: str,
        voice: str,
        speed: float,
        lang: str,
        audio_queue: "queue.Queue",
        stop_event: "threading.Event",
    ) -> None:
        """
        Synthesize one text chunk and push (audio_ndarray, sample_rate)
        tuples onto audio_queue as they become available. Must check
        stop_event periodically (between yielded pieces, at minimum)
        and stop pushing as soon as it's set. Runs on the engine's
        synthesis thread — blocking calls are fine here.
        """
        raise NotImplementedError

    def lang_for_voice(self, voice_id: str) -> str:
        info = self.voices.get(voice_id)
        return info[1] if info else "en-us"
    