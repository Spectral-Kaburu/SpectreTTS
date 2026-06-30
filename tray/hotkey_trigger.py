#!/usr/bin/env python3
"""
SpectreTTS - Hotkey Trigger
-----------------------------
This script is bound to Ctrl+Alt+R via GNOME's custom keybindings.
It does NOT load Kokoro itself (too slow to do on every keypress).
Instead it grabs the current selection and sends it over a local
Unix socket to the already-running SpectreTTS daemon (systray app),
which holds the warm model in memory and speaks immediately.

If the daemon isn't running, falls back to printing an error
via notify-send so you know to start it.
"""

import socket
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))
from selection_grabber import get_text_to_read

SOCKET_PATH = "/tmp/spectretts.sock"


def notify(message: str, urgency: str = "normal"):
    """Show a desktop notification (works regardless of session type)."""
    try:
        import subprocess
        subprocess.run(
            ["notify-send", "-u", urgency, "SpectreTTS", message],
            timeout=2
        )
    except Exception:
        pass  # notifications are a nice-to-have, never block on this


def send_to_daemon(command: str, text: str = ""):
    """Send a command to the running daemon via Unix socket."""
    if not os.path.exists(SOCKET_PATH):
        notify("Daemon not running. Start SpectreTTS first.", "critical")
        return False

    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(3)
        client.connect(SOCKET_PATH)

        payload = f"{command}|{text}"
        client.sendall(payload.encode("utf-8"))
        client.close()
        return True
    except (ConnectionRefusedError, socket.timeout, OSError) as e:
        notify(f"Couldn't reach daemon: {e}", "critical")
        return False


def main():
    text = get_text_to_read()

    if not text:
        notify("No text selected or copied.", "low")
        return

    # Truncate notification preview, but send full text to daemon
    preview = text[:60] + ("..." if len(text) > 60 else "")
    ok = send_to_daemon("speak", text)

    if ok:
        notify(f"Reading: {preview}", "low")


if __name__ == "__main__":
    main()