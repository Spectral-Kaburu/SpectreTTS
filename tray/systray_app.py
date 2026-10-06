"""
SpectreTTS - Systray Application
-----------------------------------
The visible part of the daemon. Draws a tray icon in the GNOME top bar
with a dropdown menu for:
  - Pause / Resume / Stop
  - Engine picker (radio submenu — hot-swaps the active TTS backend)
  - Voice picker (radio submenu — rebuilt when the engine changes)
  - Speed picker (radio submenu)
  - Read Clipboard (grabs the system clipboard, speaks it)
  - Recent (last few things read — SpectreTTS's own internal clipboard
    ring buffer, see engine/clipboard_store.py — reusable without
    needing X11 clipboard history)
  - Reader Window (toggle — karaoke-style word highlighting, timed
    against engine/word_timing.py's estimate since the backends don't
    give us real per-word timestamps)
  - Quit

Icon swaps between idle/speaking states so you have a visual cue even
without opening the menu.
"""

import gi

gi.require_version("Gtk", "3.0")

# Prefer Ayatana (confirmed available on BLACKBOXX), fall back to the
# original AppIndicator3 namespace for portability to other distros.
try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator3
except ValueError:
    gi.require_version("AppIndicator3", "0.1")
    from gi.repository import AppIndicator3

from gi.repository import Gtk, GLib
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engine.tts_engine import VOICES, BACKENDS
from engine.selection_grabber import get_clipboard_text
from tray.reader_window import ReaderWindow

APP_ID = "spectretts"
ICON_DIR = PROJECT_ROOT / "assets"
ICON_IDLE = str(ICON_DIR / "icon_idle.svg")
ICON_SPEAKING = str(ICON_DIR / "icon_speaking.svg")

SPEED_OPTIONS = [0.75, 1.0, 1.25, 1.5, 1.75, 2.0]


class SpectreTray:
    """
    Owns the AppIndicator, the GTK menu, and polls the engine's
    speaking state to swap icons. Runs on the GTK main thread.
    """

    def __init__(self, engine):
        self.engine = engine
        self.reader = ReaderWindow()

        self.indicator = AppIndicator3.Indicator.new(
            APP_ID,
            ICON_IDLE,
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_title("SpectreTTS")

        self.menu = Gtk.Menu()
        self._build_menu()
        self.indicator.set_menu(self.menu)

        # Wire the engine's karaoke hooks to the reader window. Both
        # fire from background threads (a Timer thread for words, the
        # calling thread — usually the socket server's — for speech
        # start), so GTK widget calls are marshalled onto the main
        # loop via GLib.idle_add rather than touched directly.
        self.engine.set_speech_started_callback(self._on_speech_started)
        self.engine.set_word_callback(self._on_word)

        # Poll engine state every 500ms to keep icon + menu labels in sync
        GLib.timeout_add(500, self._tick)

    # ── Menu construction ─────────────────────────────────────────────────────

    def _build_menu(self):
        # Status label (non-clickable, just shows current state)
        self.status_item = Gtk.MenuItem(label="Idle")
        self.status_item.set_sensitive(False)
        self.menu.append(self.status_item)

        self.menu.append(Gtk.SeparatorMenuItem())

        # Pause/Resume toggle
        self.pause_item = Gtk.MenuItem(label="Pause")
        self.pause_item.connect("activate", self._on_pause_resume)
        self.menu.append(self.pause_item)

        # Stop
        stop_item = Gtk.MenuItem(label="Stop")
        stop_item.connect("activate", self._on_stop)
        self.menu.append(stop_item)

        self.menu.append(Gtk.SeparatorMenuItem())

        # Engine submenu — radio buttons, one per backend ID.
        # Selecting one hot-swaps the backend and rebuilds the Voice submenu.
        engine_item = Gtk.MenuItem(label="Engine")
        self._engine_submenu = Gtk.Menu()
        self._engine_group = []
        self._engine_radios: dict[str, Gtk.RadioMenuItem] = {}
        for backend_id in BACKENDS:
            label = backend_id.capitalize()
            radio = Gtk.RadioMenuItem.new_with_label(self._engine_group, label)
            self._engine_group = radio.get_group()
            if backend_id == self.engine.backend.id:
                radio.set_active(True)
            radio.connect("toggled", self._on_engine_selected, backend_id)
            self._engine_submenu.append(radio)
            self._engine_radios[backend_id] = radio
        engine_item.set_submenu(self._engine_submenu)
        self.menu.append(engine_item)

        # Voice submenu — rebuilt whenever the backend changes.
        self.voice_item = Gtk.MenuItem(label="Voice")
        self._voice_submenu = Gtk.Menu()
        self.voice_item.set_submenu(self._voice_submenu)
        self.menu.append(self.voice_item)
        self._rebuild_voice_submenu()

        # Speed submenu
        speed_item = Gtk.MenuItem(label="Speed")
        speed_submenu = Gtk.Menu()
        speed_group = []
        for speed in SPEED_OPTIONS:
            radio = Gtk.RadioMenuItem.new_with_label(speed_group, f"{speed}x")
            speed_group = radio.get_group()
            if speed == self.engine.speed:
                radio.set_active(True)
            radio.connect("toggled", self._on_speed_selected, speed)
            speed_submenu.append(radio)
        speed_item.set_submenu(speed_submenu)
        self.menu.append(speed_item)

        self.menu.append(Gtk.SeparatorMenuItem())

        # Read Clipboard — grabs the system clipboard directly (bypasses
        # PRIMARY selection entirely), pushes it into our own ring
        # buffer, and speaks it. For when there's nothing highlighted
        # to Ctrl+Alt+R but you did just copy something.
        read_clipboard_item = Gtk.MenuItem(label="Read Clipboard")
        read_clipboard_item.connect("activate", self._on_read_clipboard)
        self.menu.append(read_clipboard_item)

        # Recent — SpectreTTS's own last-5 ring buffer (engine.clipboard),
        # rebuilt fresh every time the submenu opens so it always
        # reflects whatever's actually in the buffer right now.
        self.recent_item = Gtk.MenuItem(label="Recent")
        self.recent_submenu = Gtk.Menu()
        self.recent_submenu.connect("show", self._on_recent_submenu_show)
        self.recent_item.set_submenu(self.recent_submenu)
        self.menu.append(self.recent_item)

        self.menu.append(Gtk.SeparatorMenuItem())

        # Reader Window — toggles the karaoke-highlight window. Checkbox
        # item so its own state reflects whether the window is currently
        # open, including if it was closed via its own X button.
        self.reader_item = Gtk.CheckMenuItem(label="Reader Window")
        self.reader_item.connect("toggled", self._on_reader_toggled)
        self.menu.append(self.reader_item)

        self.menu.append(Gtk.SeparatorMenuItem())

        # Quit
        quit_item = Gtk.MenuItem(label="Quit SpectreTTS")
        quit_item.connect("activate", self._on_quit)
        self.menu.append(quit_item)

        self.menu.show_all()

    def _rebuild_voice_submenu(self):
        """
        Clear and repopulate the Voice submenu from the currently-active
        backend's voice list. Called once at menu construction and again
        whenever the backend is swapped at runtime, so the voices shown
        always match the engine that's actually running.
        """
        for child in self._voice_submenu.get_children():
            self._voice_submenu.remove(child)

        voice_group = []
        for voice_id, (name, locale, gender) in self.engine.backend.voices.items():
            label = f"{name} ({locale}, {gender})"
            radio = Gtk.RadioMenuItem.new_with_label(voice_group, label)
            voice_group = radio.get_group()
            if voice_id == self.engine.voice:
                radio.set_active(True)
            radio.connect("toggled", self._on_voice_selected, voice_id)
            self._voice_submenu.append(radio)

        self._voice_submenu.show_all()

    # ── Menu callbacks ────────────────────────────────────────────────────────

    def _on_pause_resume(self, _widget):
        state = self.engine.toggle_pause()
        self.pause_item.set_label("Resume" if state == "paused" else "Pause")

    def _on_stop(self, _widget):
        self.engine.stop()
        self.pause_item.set_label("Pause")

    def _on_voice_selected(self, widget, voice_id):
        if widget.get_active():
            self.engine.set_voice(voice_id)

    def _on_engine_selected(self, widget, backend_id):
        if not widget.get_active():
            return
        if backend_id == self.engine.backend.id:
            return   # already on this backend — nothing to do
        try:
            self.engine.switch_backend(backend_id)
        except ValueError as exc:
            print(f"[SpectreTTS/tray] Engine switch failed: {exc}")
            return
        # Rebuild the Voice submenu on the GTK main thread.
        # switch_backend() already ran stop(), so no audio is in flight.
        GLib.idle_add(self._rebuild_voice_submenu)

    def _on_speed_selected(self, widget, speed):
        if widget.get_active():
            self.engine.set_speed(speed)

    def _on_quit(self, _widget):
        self.engine.stop()
        Gtk.main_quit()

    def _on_read_clipboard(self, _widget):
        """Grabs the system CLIPBOARD (not PRIMARY selection — this is
        the 'I just Ctrl+C'd something' case) and speaks it directly."""
        text = get_clipboard_text()
        if text:
            self.engine.speak(text)

    def _on_recent_submenu_show(self, submenu):
        """Rebuilds the Recent submenu from engine.clipboard right
        before it's displayed, so it always reflects the current ring
        buffer contents (last 5, most recent first) rather than
        whatever it happened to look like at daemon startup."""
        for child in submenu.get_children():
            submenu.remove(child)

        entries = self.engine.clipboard.list()
        if not entries:
            empty_item = Gtk.MenuItem(label="(nothing read yet)")
            empty_item.set_sensitive(False)
            submenu.append(empty_item)
        else:
            for entry in entries:
                preview = entry["text"][:50].replace("\n", " ")
                if len(entry["text"]) > 50:
                    preview += "…"
                item = Gtk.MenuItem(label=preview)
                item.connect("activate", self._on_recent_selected, entry["text"])
                submenu.append(item)

        submenu.show_all()

    def _on_recent_selected(self, _widget, text):
        self.engine.speak(text)

    def _on_reader_toggled(self, widget):
        if widget.get_active():
            self.reader.set_text(self.engine.clipboard.get())
            self.reader.show()
        else:
            self.reader.hide()

    # ── Karaoke hooks (called from background threads — marshal via idle_add) ──

    def _on_speech_started(self, text):
        GLib.idle_add(self._apply_reader_text, text)

    def _apply_reader_text(self, text):
        self.reader.set_text(text)
        return False   # one-shot idle callback, don't repeat

    def _on_word(self, char_start, char_end):
        GLib.idle_add(self._apply_word_highlight, char_start, char_end)

    def _apply_word_highlight(self, char_start, char_end):
        self.reader.highlight_span(char_start, char_end)
        return False

    # ── State polling ─────────────────────────────────────────────────────────

    def _tick(self):
        """Called every 500ms by GLib. Syncs icon + status label to engine state."""
        if self.engine.is_speaking:
            self.indicator.set_icon_full(ICON_SPEAKING, "Speaking")
            self.status_item.set_label("Speaking...")
        else:
            self.indicator.set_icon_full(ICON_IDLE, "Idle")
            self.status_item.set_label("Idle")
            self.pause_item.set_label("Pause")   # reset label when nothing's playing

        # The reader window can be closed via its own X button (which
        # just hides it, see reader_window.py), so the checkbox item
        # can drift out of sync with reality — only push a correction
        # when it actually has, to avoid redundant show()/hide() calls
        # on every single tick.
        reader_visible = self.reader.is_visible
        if self.reader_item.get_active() != reader_visible:
            self.reader_item.set_active(reader_visible)

        return True   # keep the timeout alive
    