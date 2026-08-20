#!/usr/bin/env python3
"""
SpectreTTS - CLI
-------------------
Standalone command-line client for the SpectreTTS daemon. Talks over
the same Unix socket as hotkey_trigger.py and the tray UI — this is
just a third way in, for scripting (piping text from other tools,
calling it from shell scripts/cron, etc.) without needing a hotkey
press or the tray menu.

Not currently wired into anything — run it directly:

    python3 tray/spectre_cli.py speak "hello there"
    echo "hello there" | python3 tray/spectre_cli.py speak
    python3 tray/spectre_cli.py load "draft text to review, not read yet"
    python3 tray/spectre_cli.py pause
    python3 tray/spectre_cli.py resume
    python3 tray/spectre_cli.py stop
    python3 tray/spectre_cli.py voice af_heart
    python3 tray/spectre_cli.py speed 1.25

Text can be passed as an argument or piped via stdin — if both stdin
is piped AND text is given as an argument, the argument wins.

Exits 0 on success, 1 if the daemon isn't reachable or the command
was rejected.
"""

import argparse
import socket
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.configs import get_socket_path

SOCKET_PATH = get_socket_path()

# Commands that take a text payload vs. ones that don't
TEXT_COMMANDS = {"speak", "load", "voice", "speed"}


def send_to_daemon(command: str, payload: str = "") -> bool:
    """Sends one COMMAND|payload message to the daemon. Returns False
    (and prints why) if the daemon isn't running or the send failed."""
    if not os.path.exists(SOCKET_PATH):
        print(f"error: daemon not running (no socket at {SOCKET_PATH})", file=sys.stderr)
        return False

    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(3)
        client.connect(SOCKET_PATH)
        client.sendall(f"{command}|{payload}".encode("utf-8"))
        client.close()
        return True
    except (ConnectionRefusedError, socket.timeout, OSError) as e:
        print(f"error: couldn't reach daemon: {e}", file=sys.stderr)
        return False


def resolve_text(args_text: str | None) -> str:
    """Argument text wins; otherwise falls back to stdin if it's piped
    (not an interactive terminal, so this never hangs waiting on a
    keypress if the user forgot to pass text)."""
    if args_text:
        return args_text
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""


def main():
    parser = argparse.ArgumentParser(
        description="Send a command to the running SpectreTTS daemon."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    speak_p = subparsers.add_parser("speak", help="Speak text now (interrupts anything currently playing)")
    speak_p.add_argument("text", nargs="?", help="Text to speak (or pipe via stdin)")

    load_p = subparsers.add_parser("load", help="Stage text into the reader/Recent list WITHOUT speaking it")
    load_p.add_argument("text", nargs="?", help="Text to load (or pipe via stdin)")

    subparsers.add_parser("pause", help="Pause playback")
    subparsers.add_parser("resume", help="Resume paused playback")
    subparsers.add_parser("stop", help="Stop playback immediately")

    voice_p = subparsers.add_parser("voice", help="Switch voice")
    voice_p.add_argument("voice_id", help="Voice identifier, e.g. af_heart")

    speed_p = subparsers.add_parser("speed", help="Set playback speed")
    speed_p.add_argument("value", type=float, help="Speed multiplier, e.g. 1.25")

    args = parser.parse_args()

    if args.command in ("speak", "load"):
        text = resolve_text(args.text)
        if not text:
            print("error: no text given (pass as an argument or pipe via stdin)", file=sys.stderr)
            sys.exit(1)
        ok = send_to_daemon(args.command, text)
    elif args.command == "voice":
        ok = send_to_daemon("voice", args.voice_id)
    elif args.command == "speed":
        ok = send_to_daemon("speed", str(args.value))
    else:
        ok = send_to_daemon(args.command)

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
