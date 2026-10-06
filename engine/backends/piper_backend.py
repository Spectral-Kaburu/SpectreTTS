"""
Piper-TTS backend. Adapter for rhasspy/piper-tts
(pip install piper-tts / https://github.com/rhasspy/piper).

Key differences from the Pocket/Kokoro backends:

  - No PyTorch — piper uses onnxruntime directly. Much lighter to install.
  - Models are NOT bundled; each voice is a pair of files (.onnx + .onnx.json)
    that must exist on disk. This backend auto-downloads the configured voice
    from HuggingFace (rhasspy/piper-voices) on first use via huggingface_hub,
    storing them in ~/.cache/spectretts/piper/.
  - synthesize_stream_raw() yields raw int16 PCM bytes, not float32 arrays,
    so we convert before pushing onto the audio queue.
  - espeak-ng must be installed system-wide (apt install espeak-ng) — piper
    shells out to it for phonemization.
  - Speed control: piper exposes a length_scale parameter (inverse of speed,
    so speed=1.5 → length_scale=1/1.5≈0.67). Supported here.

Configuring the voice:
  Set SPECTRETTS_PIPER_VOICE in .env or the environment to one of the voice
  IDs listed in VOICES below (e.g. "en_US-lessac-medium"). You can also set
  SPECTRETTS_PIPER_MODEL_DIR to point at a local directory that already
  contains the .onnx/.onnx.json files, in which case no download happens.
"""

import os
import threading
import queue
from pathlib import Path

import numpy as np

from .base import TTSBackend

# ── Voice catalogue ───────────────────────────────────────────────────────────
# A curated selection of English voices across quality tiers.
# Format: voice_id -> (display_name, locale, gender)
# Voice IDs match the HuggingFace path segment under rhasspy/piper-voices,
# e.g. "en/en_US/lessac/medium" → voice_id "en_US-lessac-medium".
VOICES = {
    # English (US)
    "en_US-lessac-medium":   ("Lessac (US, medium)",   "en-us", "male"),
    "en_US-lessac-high":     ("Lessac (US, high)",     "en-us", "male"),
    "en_US-libritts-high":   ("LibriTTS (US, high)",   "en-us", "neutral"),
    "en_US-amy-low":         ("Amy (US, low)",          "en-us", "female"),
    "en_US-amy-medium":      ("Amy (US, medium)",       "en-us", "female"),
    "en_US-joe-medium":      ("Joe (US, medium)",       "en-us", "male"),
    "en_US-kusal-medium":    ("Kusal (US, medium)",     "en-us", "male"),
    "en_US-ryan-high":       ("Ryan (US, high)",        "en-us", "male"),
    # English (GB)
    "en_GB-alan-low":        ("Alan (GB, low)",         "en-gb", "male"),
    "en_GB-alan-medium":     ("Alan (GB, medium)",      "en-gb", "male"),
    "en_GB-alba-medium":     ("Alba (GB, medium)",      "en-gb", "female"),
    "en_GB-vctk-medium":     ("VCTK (GB, medium)",      "en-gb", "neutral"),
}

DEFAULT_VOICE = os.environ.get("SPECTRETTS_PIPER_VOICE", "en_US-lessac-medium")

# Where to cache downloaded model files
DEFAULT_MODEL_DIR = Path.home() / ".cache" / "spectretts" / "piper"

# HuggingFace repo hosting all piper voice files
HF_REPO = "rhasspy/piper-voices"


def _voice_id_to_hf_path(voice_id: str) -> tuple[str, str]:
    """
    Convert a voice_id like "en_US-lessac-medium" to the HF path components:
      lang_prefix = "en"
      hf_subdir   = "en/en_US/lessac/medium"
    Returns (onnx_path_in_repo, json_path_in_repo).
    """
    # voice_id format: {lang}_{REGION}-{name}-{quality}
    # e.g. en_US-lessac-medium → lang=en, region=US, name=lessac, quality=medium
    parts = voice_id.split("-")
    if len(parts) < 3:
        raise ValueError(f"Unrecognised piper voice_id format: '{voice_id}'")
    lang_region = parts[0]          # "en_US"
    name = "-".join(parts[1:-1])    # "lessac" (handles hyphenated names)
    quality = parts[-1]             # "medium"
    lang = lang_region.split("_")[0].lower()  # "en"

    base = f"{lang}/{lang_region}/{name}/{quality}/{lang_region}-{name}-{quality}"
    return f"{base}.onnx", f"{base}.onnx.json"


def _ensure_model(voice_id: str, model_dir: Path) -> tuple[Path, Path]:
    """
    Make sure the .onnx and .onnx.json files for voice_id exist in model_dir.
    Downloads from HuggingFace if missing. Returns (onnx_path, json_path).
    """
    model_dir.mkdir(parents=True, exist_ok=True)
    onnx_file = model_dir / f"{voice_id}.onnx"
    json_file = model_dir / f"{voice_id}.onnx.json"

    if not onnx_file.exists() or not json_file.exists():
        try:
            from huggingface_hub import hf_hub_download
        except ImportError:
            raise RuntimeError(
                "huggingface_hub is required to auto-download piper voices. "
                "Run: pip install huggingface-hub"
            )

        onnx_hf, json_hf = _voice_id_to_hf_path(voice_id)

        print(f"[SpectreTTS/piper] Downloading voice '{voice_id}' from HuggingFace...")
        for hf_path, local_path in [(onnx_hf, onnx_file), (json_hf, json_file)]:
            if not local_path.exists():
                downloaded = hf_hub_download(
                    repo_id=HF_REPO,
                    filename=hf_path,
                    local_dir=str(model_dir),
                    local_dir_use_symlinks=False,
                )
                # hf_hub_download may nest files; move to flat location if needed
                downloaded = Path(downloaded)
                if downloaded != local_path:
                    downloaded.rename(local_path)
        print(f"[SpectreTTS/piper] Voice '{voice_id}' ready at {model_dir}")

    return onnx_file, json_file


class PiperBackend(TTSBackend):
    id = "piper"
    voices = VOICES
    default_voice = DEFAULT_VOICE
    supports_speed = True

    def __init__(self):
        model_dir_env = os.environ.get("SPECTRETTS_PIPER_MODEL_DIR", "")
        self._model_dir = Path(model_dir_env) if model_dir_env else DEFAULT_MODEL_DIR
        self._voices_loaded: dict = {}   # voice_id -> PiperVoice instance
        self._lock = threading.Lock()

    def load(self):
        # Piper loads per-voice, not a single global model — nothing to do here.
        # Actual loading happens lazily in _get_voice().
        return self

    def _get_voice(self, voice_id: str):
        """Load (and cache) a PiperVoice for voice_id."""
        cached = self._voices_loaded.get(voice_id)
        if cached is not None:
            return cached

        with self._lock:
            cached = self._voices_loaded.get(voice_id)
            if cached is not None:
                return cached

            try:
                from piper.voice import PiperVoice
            except ImportError:
                raise RuntimeError(
                    "piper-tts is not installed. Run: pip install piper-tts"
                )

            onnx_path, _json_path = _ensure_model(voice_id, self._model_dir)
            print(f"[SpectreTTS/piper] Loading voice '{voice_id}'...")
            piper_voice = PiperVoice.load(str(onnx_path), use_cuda=False)
            self._voices_loaded[voice_id] = piper_voice
            print(f"[SpectreTTS/piper] Voice '{voice_id}' loaded. "
                  f"Sample rate: {piper_voice.config.sample_rate} Hz")
            return piper_voice

    def synthesize_chunk(
        self,
        chunk: str,
        voice: str,
        speed: float,
        lang: str,
        audio_queue: "queue.Queue",
        stop_event: "threading.Event",
    ) -> None:
        piper_voice = self._get_voice(voice)
        sample_rate = piper_voice.config.sample_rate

        # piper length_scale is the inverse of playback speed:
        #   speed=1.0 → length_scale=1.0 (normal)
        #   speed=1.5 → length_scale≈0.67 (faster)
        #   speed=0.8 → length_scale=1.25 (slower)
        length_scale = 1.0 / max(speed, 0.1)
        
        from piper.voice import SynthesisConfig
        syn_config = SynthesisConfig(length_scale=length_scale)

        # synthesize() yields AudioChunk objects
        for chunk_obj in piper_voice.synthesize(
            chunk, syn_config=syn_config
        ):
            if stop_event.is_set():
                break
            audio_array = chunk_obj.audio_float_array
            if audio_array is not None and len(audio_array) > 0:
                audio_queue.put((audio_array, sample_rate))
