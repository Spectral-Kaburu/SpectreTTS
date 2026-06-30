#!/usr/bin/env python3
"""
SpectreTTS - Daemon Entrypoint
---------------------------------
The single process that should be running at all times (ideally
autostarted at login). It:

  1. Loads the TTSEngine (Kokoro pipeline loads lazily on first speak)
  2. Starts the Unix socket server (hotkey_trigger.py talks to this)
  3. Draws the systray icon
  4. Runs the GTK main loop, which keeps everything alive

Run with:
    python tray/daemon.py

Stop with the tray menu's "Quit" item, or Ctrl+C in the terminal.
"""

import os
import sys
import signal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from engine.tts_engine import TTSEngine
from engine.socket_server import SocketServer
from tray.systray_app import SpectreTray


def main():
    print("[SpectreTTS] Starting daemon...")

    # 1. Core engine — model loads lazily on first .speak() call,
    #    so startup here is instant.
    engine = TTSEngine()

    # 2. Socket server — listens for hotkey_trigger.py and other clients
    socket_server = SocketServer(engine)
    socket_server.start()

    # 3. Systray icon + menu
    tray = SpectreTray(engine)

    print("[SpectreTTS] Daemon ready. Tray icon active, socket listening.")
    print("[SpectreTTS] Press Ctrl+Alt+R on any highlighted text to read it.")

    # Allow Ctrl+C in the terminal to cleanly exit the GTK main loop
    def handle_sigint(_sig, _frame):
        print("\n[SpectreTTS] Shutting down...")
        engine.stop()
        socket_server.stop()
        Gtk.main_quit()

    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigint)

    # GLib/GTK main loop blocks here until Quit is selected or signal received.
    # We need a periodic no-op so Python gets to process signals while GTK
    # is blocked in its own C event loop (otherwise SIGINT is swallowed).
    from gi.repository import GLib
    GLib.timeout_add(200, lambda: True)

    try:
        Gtk.main()
    finally:
        socket_server.stop()
        print("[SpectreTTS] Stopped.")


if __name__ == "__main__":
    main()
    