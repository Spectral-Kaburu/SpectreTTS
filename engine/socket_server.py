"""
SpectreTTS - Socket Server
---------------------------
Runs inside the main daemon process. Listens on a Unix domain socket
for commands from:
  - hotkey_trigger.py (Ctrl+Alt+R presses)
  - the systray UI itself
  - future: any other local script/project that wants to trigger speech

Protocol: simple pipe-delimited text. "COMMAND|payload"
Commands: speak, pause, resume, stop, voice, speed
"""

import socket
import os
import threading

from .configs import get_socket_path

SOCKET_PATH = get_socket_path()


class SocketServer:
    """
    Background thread that listens for incoming commands and
    dispatches them to the TTSEngine instance.
    """

    def __init__(self, engine):
        self.engine = engine
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
            data = conn.recv(65536).decode("utf-8")
            if not data:
                return

            parts = data.split("|", 1)
            command = parts[0].strip()
            payload = parts[1] if len(parts) > 1 else ""

            self._dispatch(command, payload)

        except Exception as e:
            print(f"[SpectreTTS] Socket handler error: {e}")
        finally:
            conn.close()

    def _dispatch(self, command: str, payload: str):
        if command == "speak":
            self.engine.speak(payload)
        elif command == "pause":
            self.engine.pause()
        elif command == "resume":
            self.engine.resume()
        elif command == "stop":
            self.engine.stop()
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
            print(f"[SpectreTTS] Unknown command: {command}")

