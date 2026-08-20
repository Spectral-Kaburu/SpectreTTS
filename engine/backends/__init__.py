import os

from .base import TTSBackend
from .kokoro_backend import KokoroBackend
from .pocket_backend import PocketBackend

_REGISTRY = {
    "kokoro": KokoroBackend,
    "pocket": PocketBackend,
}


def get_backend(name: str = None) -> TTSBackend:
    """
    Resolve which backend to instantiate.

    Priority: explicit `name` arg > SPECTRETTS_BACKEND env var >
    "kokoro" (unchanged default so existing installs/configs keep
    working exactly as before with zero changes required).
    """
    key = (name or os.environ.get("SPECTRETTS_BACKEND", "kokoro")).strip().lower()
    try:
        backend_cls = _REGISTRY[key]
    except KeyError:
        raise ValueError(
            f"Unknown SpectreTTS backend '{key}'. Available: {', '.join(_REGISTRY)}"
        )
    return backend_cls()


__all__ = ["TTSBackend", "KokoroBackend", "PocketBackend", "get_backend"]
