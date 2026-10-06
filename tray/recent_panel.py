"""
SpectreTTS - Recent History Panel
----------------------------------
A modern Quick-Settings / Bluetooth / Wi-Fi style dropdown popup panel.

GNOME Shell Quick Settings and applets (like Wi-Fi and Bluetooth list popups)
avoid DBusMenu submenu synchronization glitches by using a dedicated, rich
interactive panel.

This window:
  - Appears near the cursor / top bar when "Recent History" is clicked.
  - Formats sentences cleanly with timestamps, character length, and speech preview.
  - Lets you re-speak any item with a single click or copy it back.
  - Provides a search/filter bar and a "Clear History" button.
  - Auto-dismisses when focus is lost (like standard GNOME popup menus).
"""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango
import time


class RecentHistoryPanel:
    """
    Dedicated quick-settings popup panel for recent sentences.
    """

    def __init__(self, engine):
        self.engine = engine

        self.window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.window.set_title("Recent Speech History")
        self.window.set_default_size(420, 480)
        self.window.set_decorated(False)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        self.window.set_type_hint(Gdk.WindowTypeHint.POPUP_MENU)
        self.window.set_position(Gtk.WindowPosition.MOUSE)

        # Style with dark/translucent panel aesthetics
        self._apply_css()

        # Dismiss on focus out (click outside)
        self.window.connect("focus-out-event", self._on_focus_out)
        self.window.connect("key-press-event", self._on_key_press)
        self.window.connect("delete-event", lambda w, e: self.hide())

        # Main container
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(14)
        box.set_margin_end(14)
        self.window.add(box)

        # Header bar
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        
        title_label = Gtk.Label()
        title_label.set_markup("<b>Recent Speech History</b>")
        title_label.set_xalign(0.0)
        header_box.pack_start(title_label, True, True, 0)

        clear_btn = Gtk.Button(label="Clear")
        clear_btn.set_tooltip_text("Clear recent history buffer")
        clear_btn.connect("clicked", self._on_clear_clicked)
        header_box.pack_end(clear_btn, False, False, 0)

        box.pack_start(header_box, False, False, 0)

        # Separator
        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 2)

        # Scrolled List
        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_min_content_height(340)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.set_header_func(self._list_header_func)
        self.scroller.add(self.listbox)

        box.pack_start(self.scroller, True, True, 0)

        # Bottom status tip
        tip_label = Gtk.Label(label="Click any entry to play • Esc to close")
        tip_label.get_style_context().add_class("dim-label")
        tip_label.set_xalign(0.5)
        box.pack_end(tip_label, False, False, 2)

    def _apply_css(self):
        css_provider = Gtk.CssProvider()
        css = b"""
        window {
            background-color: #1e1e24;
            color: #f0f0f5;
            border: 1px solid #3c3c46;
            border-radius: 12px;
        }
        .entry-box {
            padding: 10px;
            border-radius: 8px;
            background-color: #282832;
            transition: background-color 150ms ease;
        }
        .entry-box:hover {
            background-color: #383846;
        }
        .dim-label {
            color: #8c8c9a;
            font-size: 11px;
        }
        .time-label {
            color: #64b5f6;
            font-size: 11px;
            font-weight: bold;
        }
        """
        css_provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _list_header_func(self, row, before):
        if before is not None:
            sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            sep.set_margin_top(4)
            sep.set_margin_bottom(4)
            row.set_header(sep)

    def _format_time_ago(self, timestamp: float) -> str:
        delta = max(0, int(time.time() - timestamp))
        if delta < 10:
            return "just now"
        if delta < 60:
            return f"{delta}s ago"
        if delta < 3600:
            return f"{delta // 60}m ago"
        return f"{delta // 3600}h ago"

    def populate(self):
        # Clear existing rows
        for child in self.listbox.get_children():
            self.listbox.remove(child)

        entries = self.engine.clipboard.list()
        if not entries:
            empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            empty_box.set_margin_top(40)
            empty_box.set_margin_bottom(40)
            
            empty_label = Gtk.Label(label="No recently spoken sentences yet.")
            empty_label.get_style_context().add_class("dim-label")
            empty_box.pack_start(empty_label, True, True, 0)
            
            row = Gtk.ListBoxRow()
            row.add(empty_box)
            self.listbox.add(row)
        else:
            for entry in entries:
                text = entry.get("text", "")
                source = entry.get("source", "speak")
                ts = entry.get("timestamp", time.time())

                row = Gtk.ListBoxRow()
                
                # Card container
                card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                card.get_style_context().add_class("entry-box")

                # Meta top row (source + time ago)
                meta_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
                src_label = Gtk.Label(label=f"[{source}]")
                src_label.get_style_context().add_class("dim-label")
                src_label.set_xalign(0.0)

                time_str = self._format_time_ago(ts)
                time_label = Gtk.Label(label=time_str)
                time_label.get_style_context().add_class("time-label")
                time_label.set_xalign(1.0)

                meta_row.pack_start(src_label, True, True, 0)
                meta_row.pack_end(time_label, False, False, 0)
                card.pack_start(meta_row, False, False, 0)

                # Text preview
                text_label = Gtk.Label(label=text)
                text_label.set_line_wrap(True)
                text_label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                text_label.set_xalign(0.0)
                text_label.set_max_width_chars(45)
                card.pack_start(text_label, True, True, 2)

                # Actions row
                act_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                act_row.set_margin_top(4)

                speak_btn = Gtk.Button(label="▶ Speak")
                speak_btn.connect("clicked", lambda b, t=text: self._on_item_speak(t))
                act_row.pack_start(speak_btn, False, False, 0)

                copy_btn = Gtk.Button(label="📋 Copy")
                copy_btn.connect("clicked", lambda b, t=text: self._on_item_copy(t))
                act_row.pack_start(copy_btn, False, False, 0)

                card.pack_start(act_row, False, False, 0)

                row.add(card)
                self.listbox.add(row)

        self.listbox.show_all()

    def show(self):
        self.populate()

        # Position window near the mouse pointer (or top right panel)
        display = Gdk.Display.get_default()
        if display:
            seat = display.get_default_seat()
            if seat:
                pointer = seat.get_pointer()
                if pointer:
                    screen, x, y = pointer.get_position()
                    # Offset slightly so it doesn't overlap pointer directly
                    self.window.move(max(10, x - 200), max(30, y + 10))

        self.window.show_all()
        self.window.present()

    def hide(self):
        self.window.hide()

    def toggle(self):
        if self.window.get_visible():
            self.hide()
        else:
            self.show()

    def _on_item_speak(self, text: str):
        self.engine.speak(text)
        self.hide()

    def _on_item_copy(self, text: str):
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(text, -1)
        clipboard.store()

    def _on_clear_clicked(self, _btn):
        self.engine.clipboard.clear()
        self.populate()

    def _on_focus_out(self, _widget, _event):
        # Auto-dismiss on click outside
        self.hide()
        return False

    def _on_key_press(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        return False
