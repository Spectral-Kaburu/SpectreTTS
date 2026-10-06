from ..configs import get_backend_name
from .base import TTSBackend
from .kokoro_backend import KokoroBackend
from .pocket_backend import PocketBackend
from .piper_backend import PiperBackend

_REGISTRY = {
    "kokoro": KokoroBackend,
    "pocket": PocketBackend,
    "piper":  PiperBackend,
}


def get_backend(name: str = None) -> TTSBackend:
    """
    Resolve which backend to instantiate.

    Priority: explicit `name` arg > SPECTRETTS_BACKEND env var (via config) >
    "pocket" (default — see engine/configs.py).
    Available backends: "kokoro", "pocket", "piper".
    """
    key = (name or get_backend_name()).strip().lower()
    try:
        backend_cls = _REGISTRY[key]
    except KeyError:
        raise ValueError(
            f"Unknown SpectreTTS backend '{key}'. Available: {', '.join(_REGISTRY)}"
        )
    return backend_cls()


__all__ = ["TTSBackend", "KokoroBackend", "PocketBackend", "PiperBackend", "get_backend"]
