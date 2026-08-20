"""
SpectreTTS - Configuration
--------------------------
Central configuration loader. Loads .env file and provides typed
accessors for all environment variables.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root (one level up from engine/)
PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def get_socket_path() -> str:
    """
    Get the Unix domain socket path for daemon communication.

    Returns:
        Path to the socket file (e.g., /tmp/spectretts.sock)
    """
    return os.environ.get("SPECTRETTS_SOCKET_PATH", "/tmp/spectretts.sock")


def get_backend_name() -> str:
    """
    Get the TTS backend name from environment.

    Returns:
        Backend identifier: "kokoro" or "pocket"
    """
    return os.environ.get("SPECTRETTS_BACKEND", "kokoro").strip().lower()