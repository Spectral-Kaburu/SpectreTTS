"""
SpectreTTS - Systray Application
-----------------------------------
The visible part of the daemon. Draws a tray icon in the GNOME top bar
with a dropdown menu for:
  - Pause / Resume / Stop
  - Voice picker (radio submenu)
  - Speed picker (radio submenu)
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.tts_engine import VOICES

APP_ID = "spectretts"
ICON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets"
)
ICON_IDLE = os.path.join(ICON_DIR, "icon_idle.svg")
ICON_SPEAKING = os.path.join(ICON_DIR, "icon_speaking.svg")

SPEED_OPTIONS = [0.75, 1.0, 1.25, 1.5, 1.75, 2.0]


class SpectreTray:
    """
    Owns the AppIndicator, the GTK menu, and polls the engine's
    speaking state to swap icons. Runs on the GTK main thread.
    """

    def __init__(self, engine):
        self.engine = engine

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

        # Voice submenu
        voice_item = Gtk.MenuItem(label="Voice")
        voice_submenu = Gtk.Menu()
        voice_group = []
        for voice_id, (name, locale, gender) in VOICES.items():
            label = f"{name} ({locale}, {gender})"
            radio = Gtk.RadioMenuItem.new_with_label(voice_group, label)
            voice_group = radio.get_group()
            if voice_id == self.engine.voice:
                radio.set_active(True)
            radio.connect("toggled", self._on_voice_selected, voice_id)
            voice_submenu.append(radio)
        voice_item.set_submenu(voice_submenu)
        self.menu.append(voice_item)

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

        # Quit
        quit_item = Gtk.MenuItem(label="Quit SpectreTTS")
        quit_item.connect("activate", self._on_quit)
        self.menu.append(quit_item)

        self.menu.show_all()

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

    def _on_speed_selected(self, widget, speed):
        if widget.get_active():
            self.engine.set_speed(speed)

    def _on_quit(self, _widget):
        self.engine.stop()
        Gtk.main_quit()

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

        return True   # keep the timeout alive
    