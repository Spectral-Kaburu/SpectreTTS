"""
SpectreTTS - Socket Server
---------------------------
Runs inside the main daemon process. Listens on a Unix domain socket
for commands from:
  - hotkey_trigger.py (Ctrl+Alt+R presses)
  - the systray UI itself
  - Annoying-sister, Aether, aximo, cron jobs, CLI scripts, etc.

Protocol: supports both Structured JSON (recommended) and legacy
pipe-delimited text for backwards compatibility.

JSON (modern):
    {"command": "speak", "source": "aether", "priority": "high",
     "text": "Your build finished.", "interrupt": false}

Legacy pipe-delimited (backwards compatible):
    speak|Hello from SpectreTTS
    stop|
    pause|
    resume|
    voice|en_US-lessac-medium
    speed|1.1

Routing:
    speak / load  → SpeechArbiter.submit()    (priority queue + pacing)
    stop          → SpeechArbiter.handle_stop()
    pause         → SpeechArbiter.handle_pause()
    resume        → SpeechArbiter.handle_resume()
    toggle_pause  → TTSEngine.toggle_pause()  (direct)
    voice         → TTSEngine.set_voice()     (direct)
    speed         → TTSEngine.set_speed()     (direct)
    status        → JSON response written back on the connection
    history       → JSON response written back on the connection
"""

import json
import os
import socket
import threading

from .configs import get_socket_path
from .gateway import SpeechArbiter, SpeechRequest

SOCKET_PATH = get_socket_path()


class SocketServer:
    """
    Background thread that listens for incoming commands and dispatches
    them to the SpeechArbiter or TTSEngine instance as appropriate.
    """

    def __init__(self, engine, arbiter: SpeechArbiter):
        self.engine  = engine
        self.arbiter = arbiter
        self._server_sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self):
        # Clean up stale socket file from a previous crashed run
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)

        self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_sock.bind(SOCKET_PATH)
        self._server_sock.listen(5)
        os.chmod(SOCKET_PATH, 0o600)   # only this user can talk to it

        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        print(f"[SpectreTTS] Socket server listening at {SOCKET_PATH}")

    def stop(self):
        self._running = False
        if self._server_sock:
            self._server_sock.close()
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)

    def _listen_loop(self):
        while self._running:
            try:
                conn, _ = self._server_sock.accept()
            except OSError:
                break   # socket closed, shutting down

            threading.Thread(
                target=self._handle_client,
                args=(conn,),
                daemon=True
            ).start()

    def _handle_client(self, conn: socket.socket):
        try:
            data = conn.recv(65536).decode("utf-8").strip()
            if not data:
                return

            # ── Protocol detection ────────────────────────────────────────────
            # A JSON payload always starts with '{'.  Everything else is treated
            # as legacy pipe-delimited for full backwards compatibility.
            if data.startswith("{"):
                self._handle_json(data, conn)
            else:
                self._handle_legacy(data, conn)

        except Exception as e:
            print(f"[SpectreTTS] Socket handler error: {e}")
        finally:
            conn.close()

    # ── JSON protocol ─────────────────────────────────────────────────────────

    def _handle_json(self, raw: str, conn: socket.socket):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._send_response(conn, {"ok": False, "error": f"Invalid JSON: {exc}"})
            return

        command = payload.get("command", "").lower()

        if command == "speak":
            req = SpeechRequest.from_json(payload)
            accepted = self.arbiter.submit(req)
            self._send_response(conn, {"ok": accepted, "id": req.id,
                                       "queued": accepted})

        elif command == "load":
            # Stages text for the reader window without speaking it.
            text = payload.get("text", "")
            self.engine.load_text(text)
            self._send_response(conn, {"ok": True})

        elif command == "stop":
            self.arbiter.handle_stop()
            self._send_response(conn, {"ok": True})

        elif command == "pause":
            self.arbiter.handle_pause()
            self._send_response(conn, {"ok": True})

        elif command == "resume":
            self.arbiter.handle_resume()
            self._send_response(conn, {"ok": True})

        elif command == "toggle_pause":
            result = self.engine.toggle_pause()
            self._send_response(conn, {"ok": True, "state": result})

        elif command == "voice":
            voice = payload.get("voice", "")
            self.engine.set_voice(voice)
            self._send_response(conn, {"ok": True})

        elif command == "speed":
            try:
                self.engine.set_speed(float(payload.get("speed", 1.0)))
                self._send_response(conn, {"ok": True})
            except (ValueError, TypeError) as exc:
                self._send_response(conn, {"ok": False, "error": str(exc)})

        elif command == "status":
            self._send_response(conn, {"ok": True, **self.arbiter.status()})

        elif command == "history":
            n = int(payload.get("n", 20))
            self._send_response(conn, {"ok": True, "records": self.arbiter.history(n)})

        else:
            self._send_response(conn, {"ok": False, "error": f"Unknown command: {command!r}"})
            print(f"[SpectreTTS] Unknown JSON command: {command!r}")

    # ── Legacy pipe-delimited protocol ────────────────────────────────────────

    def _handle_legacy(self, data: str, conn: socket.socket):
        """
        Handle old-style 'COMMAND|payload' messages.

        Legacy `speak|<text>` is automatically assigned:
            source="legacy", priority="normal", interrupt=False
        All other commands work exactly as before.
        """
        parts   = data.split("|", 1)
        command = parts[0].strip()
        payload = parts[1] if len(parts) > 1 else ""

        if command == "speak":
            req = SpeechRequest.legacy(payload)
            self.arbiter.submit(req)

        elif command == "load":
            self.engine.load_text(payload)

        elif command == "stop":
            self.arbiter.handle_stop()

        elif command == "pause":
            self.arbiter.handle_pause()

        elif command == "resume":
            self.arbiter.handle_resume()

        elif command == "toggle_pause":
            self.engine.toggle_pause()

        elif command == "voice":
            self.engine.set_voice(payload)

        elif command == "speed":
            try:
                self.engine.set_speed(float(payload))
            except ValueError:
                pass

        else:
            print(f"[SpectreTTS] Unknown legacy command: {command!r}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _send_response(conn: socket.socket, data: dict):
        """Write a JSON line back to the client (best-effort; fire-and-forget)."""
        try:
            conn.sendall((json.dumps(data) + "\n").encode("utf-8"))
        except OSError:
            pass   # client disconnected before we could reply — that's fine
