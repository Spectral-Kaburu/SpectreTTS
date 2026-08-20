"""
Pocket-TTS backend. Adapter for Kyutai's Pocket-TTS
(pip install pocket-tts / https://github.com/kyutai-labs/pocket-tts).

Shaped a bit differently from Kokoro under the hood, which is why this
isn't a one-liner:

  - Voice loading is two-step and per-voice. get_state_for_audio_prompt()
    builds a "voice state" that's slow the first time a given voice is
    used (the docs call it out explicitly as a slow op to cache), so we
    keep a dict of built voice states keyed by voice id, alongside the
    single shared model — same "load once, reuse forever" shape Kokoro
    already had, just one level deeper.
  - generate_audio_stream() is a plain synchronous Python generator over
    torch tensors, not asyncio like kokoro-onnx's create_stream(). No
    event loop needed — we just iterate it directly on the synthesis
    thread and .numpy() each piece before queueing it.
  - Voices are a small fixed catalog (not a locale/gender matrix), and
    language is chosen once at model-load time via --language /
    SPECTRETTS_POCKET_LANGUAGE, not per-call — see lang_for_voice().
"""

import os
import queue
import threading

from .base import TTSBackend

# Which pretrained language variant to load. Non-English languages also
# have bigger "_24l" variants (higher quality, slower) — e.g. "italian_24l".
POCKET_LANGUAGE = os.environ.get("SPECTRETTS_POCKET_LANGUAGE", "english")

# Kyutai's premade voice catalog (see the pocket-tts README). You can
# also pass a local wav path or an "hf://..." voice file as `voice`
# directly through set_voice() even if it's not listed here — anything
# not found in this dict is passed straight through to
# get_state_for_audio_prompt() as-is.
VOICES = {
    "alba":    ("Alba",    POCKET_LANGUAGE, "female"),
    "marius":  ("Marius",  POCKET_LANGUAGE, "male"),
    "javert":  ("Javert",  POCKET_LANGUAGE, "male"),
    "jean":    ("Jean",    POCKET_LANGUAGE, "male"),
    "fantine": ("Fantine", POCKET_LANGUAGE, "female"),
    "cosette": ("Cosette", POCKET_LANGUAGE, "female"),
    "eponine": ("Eponine", POCKET_LANGUAGE, "female"),
    "azelma":  ("Azelma",  POCKET_LANGUAGE, "female"),
}


class PocketBackend(TTSBackend):
    id = "pocket"
    voices = VOICES
    default_voice = "alba"

    # The Python API (TTSModel.generate_audio / generate_audio_stream)
    # doesn't document a per-call speed knob the way Kokoro's speed=
    # does. Rather than fake support, set_speed() becomes a no-op for
    # this backend — see TTSEngine.set_speed().
    supports_speed = False

    def __init__(self):
        self._model = None
        self._voice_states = {}
        self._lock = threading.Lock()

    def load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:  # re-check inside lock
                    from pocket_tts import TTSModel

                    print(f"[SpectreTTS] Loading Pocket-TTS model ({POCKET_LANGUAGE})...")
                    self._model = TTSModel.load_model(language=POCKET_LANGUAGE)
                    print("[SpectreTTS] Model ready.")
        return self._model

    def _get_voice_state(self, model, voice: str):
        state = self._voice_states.get(voice)
        if state is not None:
            return state
        with self._lock:
            state = self._voice_states.get(voice)
            if state is None:
                # Known preset name, or a raw local path / hf:// URI —
                # get_state_for_audio_prompt() accepts either.
                state = model.get_state_for_audio_prompt(voice)
                self._voice_states[voice] = state
        return state

    def synthesize_chunk(self, chunk, voice, speed, lang, audio_queue, stop_event):
        model = self.load()
        voice_state = self._get_voice_state(model, voice)
        sample_rate = model.sample_rate

        for audio in model.generate_audio_stream(voice_state, chunk):
            if stop_event.is_set():
                break
            samples = audio.numpy() if hasattr(audio, "numpy") else audio
            if samples is not None and len(samples) > 0:
                audio_queue.put((samples, sample_rate))
                