"""
SpectreTTS - Reader Window
-----------------------------
A small togglable GTK window that shows whatever text SpectreTTS is
currently reading (from engine.clipboard, the internal buffer — see
engine/clipboard_store.py) and highlights the current word as it's
spoken.

The highlight is TIME-ESTIMATED, not aligned to real audio (Kokoro
gives us no per-word timestamps — see engine/word_timing.py for the
estimator this is driven by). It'll drift on long passages. Treat it
as "roughly where we are", not a karaoke machine with real captions.

Hidden by default. Toggled from the tray menu ("Reader Window").
Closing the window (the X button) just hides it — the daemon keeps
running either way, same as every other tray control.
"""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango


class ReaderWindow:
    """
    Owns a single GTK window with a scrollable, read-only text view.
    `set_text()` loads new content; `highlight_span()` moves the
    highlight tag to a given char range; `clear_highlight()` removes it.
    All public methods are safe to call from any thread EXCEPT they
    must actually run on the GTK main loop — callers driven by a
    background thread (the engine's word-timer callbacks) should wrap
    calls in GLib.idle_add, which systray_app.py does.
    """

    def __init__(self):
        self.window = Gtk.Window(title="SpectreTTS Reader")
        self.window.set_default_size(480, 320)
        # Hiding instead of destroying on close — same pattern as a
        # media player's "now playing" window, so re-opening it doesn't
        # need to reconstruct the widget tree or lose scroll position.
        self.window.connect("delete-event", self._on_close)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self.text_view = Gtk.TextView()
        self.text_view.set_editable(False)
        self.text_view.set_cursor_visible(False)
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.text_view.set_left_margin(16)
        self.text_view.set_right_margin(16)
        self.text_view.set_top_margin(12)
        self.text_view.set_bottom_margin(12)
        self.text_view.modify_font(Pango.FontDescription("Sans 12"))

        self.buffer = self.text_view.get_buffer()
        self._highlight_tag = self.buffer.create_tag(
            "current_word",
            background="#ff3b30",
            foreground="#ffffff",
        )

        scroller.add(self.text_view)
        self.window.add(scroller)

        self._current_text = ""

    # ── Public API ─────────────────────────────────────────────────────────

    def set_text(self, text: str):
        """Loads new text and clears any existing highlight. Call this
        once per utterance, before the first highlight_span() for it."""
        self._current_text = text or ""
        self.buffer.set_text(self._current_text)

    def highlight_span(self, char_start: int, char_end: int):
        """Moves the highlight tag to cover [char_start, char_end) of
        whatever text was last passed to set_text()."""
        if not self._current_text:
            return
        # Guard against stale offsets from a previous utterance if
        # set_text() and highlight_span() ever race — better to skip a
        # highlight than crash on an out-of-range iter.
        length = len(self._current_text)
        if char_start < 0 or char_end > length or char_start >= char_end:
            return

        self.buffer.remove_tag(self._highlight_tag, self.buffer.get_start_iter(), self.buffer.get_end_iter())
        start_iter = self.buffer.get_iter_at_offset(char_start)
        end_iter = self.buffer.get_iter_at_offset(char_end)
        self.buffer.apply_tag(self._highlight_tag, start_iter, end_iter)

        # Keep the current word in view as it scrolls past the fold.
        self.text_view.scroll_to_iter(start_iter, 0.1, False, 0.0, 0.0)

    def clear_highlight(self):
        self.buffer.remove_tag(self._highlight_tag, self.buffer.get_start_iter(), self.buffer.get_end_iter())

    def show(self):
        self.window.show_all()
        self.window.present()

    def hide(self):
        self.window.hide()

    def toggle(self):
        if self.window.get_visible():
            self.hide()
        else:
            self.show()

    @property
    def is_visible(self) -> bool:
        return self.window.get_visible()

    # ── Internal ───────────────────────────────────────────────────────────

    def _on_close(self, widget, event):
        self.hide()
        return True   # swallow the event — don't destroy the window
