"""
SpectreTTS - Internal Clipboard
---------------------------------
SpectreTTS keeps its own small ring buffer of recently-loaded text
instead of re-querying xclip mid-speech and instead of only ever
remembering ONE thing.

Three problems this solves:

1. Race conditions: the X11 PRIMARY selection can change or clear the
   instant focus moves to another window, so re-reading it partway
   through a long speak (e.g. to redraw the reader window, or to
   figure out what word is currently playing) can silently return the
   WRONG text, or nothing at all.

2. The karaoke highlighter (reader_window.py) needs an exact, stable
   copy of the string word-timing offsets were computed against. Word
   spans are computed once, against the CURRENT slot, and stay valid
   for the whole utterance no matter what the system clipboard does
   next.

3. "What did I just have it read?" — a single slot means grabbing a
   new selection destroys the last one before you've decided you
   wanted it back. Keeping the last few lets the tray's "Recent"
   submenu offer them again without needing X11's clipboard history
   (which most window managers don't keep at all).

This is a ring buffer, most-recent-first, capped at MAX_SLOTS. It is
NOT a general-purpose clipboard manager — no pinning, no persistence
across restarts, nothing system-wide. Just "the last few things
SpectreTTS was asked to read."
"""

import threading
import time
from collections import deque


class ClipboardStore:
    """Thread-safe ring buffer of recently-loaded text entries."""

    MAX_SLOTS = 5

    def __init__(self):
        self._lock = threading.Lock()
        # Most-recent-first. deque(maxlen=...) silently drops the
        # oldest entry once full — exactly the eviction policy we want.
        self._entries: deque = deque(maxlen=self.MAX_SLOTS)

    # ── Writing ────────────────────────────────────────────────────────────

    def push(self, text: str, source: str = "manual"):
        """
        Adds `text` as the new current (most recent) slot. A duplicate
        of the current top entry is a no-op (re-speaking the same
        selection twice shouldn't burn a slot and push everything
        else down).
        """
        text = text or ""
        if not text:
            return
        with self._lock:
            if self._entries and self._entries[0]["text"] == text:
                return
            self._entries.appendleft({
                "text": text,
                "source": source,
                "timestamp": time.time(),
            })

    # ── Reading ────────────────────────────────────────────────────────────

    def get(self) -> str:
        """Returns the current (most recent) slot's text, or "" if empty."""
        with self._lock:
            return self._entries[0]["text"] if self._entries else ""

    def get_with_source(self) -> tuple[str, str]:
        with self._lock:
            if not self._entries:
                return "", "none"
            top = self._entries[0]
            return top["text"], top["source"]

    def get_at(self, index: int) -> str:
        """Text at a given slot (0 = most recent). "" if out of range."""
        with self._lock:
            if 0 <= index < len(self._entries):
                return self._entries[index]["text"]
            return ""

    def list(self) -> list[dict]:
        """
        Returns all slots, most-recent-first, as copies — safe for a
        caller (e.g. the tray's "Recent" submenu) to iterate without
        holding the lock. Each entry: {"text", "source", "timestamp"}.
        """
        with self._lock:
            return [dict(e) for e in self._entries]

    def clear(self):
        with self._lock:
            self._entries.clear()

    def is_empty(self) -> bool:
        with self._lock:
            return not self._entries

    def __len__(self):
        with self._lock:
            return len(self._entries)
