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
import logging

# Configure logging for the hotkey trigger process
logging.basicConfig(
    filename="/tmp/spectretts_hotkey.log",
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.selection_grabber import get_text_to_read
from engine.configs import get_socket_path

SOCKET_PATH = get_socket_path()


def notify(message: str, urgency: str = "normal"):
    """Show a desktop notification (works regardless of session type)."""
    logger.debug(f"Sending notification (urgency: {urgency}): {message}")
    try:
        import subprocess
        subprocess.run(
            ["notify-send", "-u", urgency, "SpectreTTS", message],
            timeout=2
        )
    except Exception as e:
        logger.warning(f"Failed to send notification: {e}")


def send_to_daemon(command: str, text: str = ""):
    """Send a command to the running daemon via Unix socket."""
    logger.debug(f"Preparing to send command '{command}' to daemon...")
    if not os.path.exists(SOCKET_PATH):
        logger.error(f"Daemon socket not found at {SOCKET_PATH}.")
        notify("Daemon not running. Start SpectreTTS first.", "critical")
        return False

    try:
        logger.debug(f"Connecting to socket {SOCKET_PATH}...")
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(3)
        client.connect(SOCKET_PATH)

        payload = f"{command}|{text}"
        logger.debug(f"Sending payload (length: {len(payload)} chars)...")
        client.sendall(payload.encode("utf-8"))
        client.close()
        logger.info(f"Successfully sent command '{command}' to daemon.")
        return True
    except (ConnectionRefusedError, socket.timeout, OSError) as e:
        logger.error(f"Socket connection failed: {e}", exc_info=True)
        notify(f"Couldn't reach daemon: {e}", "critical")
        return False


def main():
    logger.info("--- Hotkey Trigger Invoked ---")
    text = get_text_to_read()

    if not text:
        logger.warning("No text to read. Exiting.")
        notify("No text selected or copied.", "low")
        return

    # Truncate notification preview, but send full text to daemon
    preview = text[:60] + ("..." if len(text) > 60 else "")
    logger.info(f"Captured text preview: {preview}")
    
    ok = send_to_daemon("speak", text)

    if ok:
        notify(f"Reading: {preview}", "low")
    else:
        logger.error("Failed to send text to daemon.")


if __name__ == "__main__":
    main()
    