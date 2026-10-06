"""
SpectreTTS Gateway - Rate Limiter / Cooldown Manager
------------------------------------------------------
Prevents the same logical event from firing TTS repeatedly within a short
window.  Clients supply a `cooldown_key` (e.g. "idle_roast_vlc") and a
`cooldown_seconds` value.  The limiter tracks the last time each key was
*allowed through* and rejects new requests that arrive before the window
has elapsed.

Thread-safe: uses a single `threading.Lock` around the internal dict.
"""

import threading
import time


class RateLimiter:
    """
    Cooldown-based deduplication guard.

    Usage::

        limiter = RateLimiter()
        if limiter.allow("idle_roast_vlc", cooldown_seconds=30):
            # submit request
        else:
            # drop / log as cooldown_active
    """

    def __init__(self) -> None:
        # {cooldown_key: last_allowed_monotonic_time}
        self._last_seen: dict[str, float] = {}
        self._lock = threading.Lock()

    # ── Public interface ──────────────────────────────────────────────────────

    def allow(self, key: str, cooldown_seconds: int | float) -> bool:
        """
        Return True and record *now* as the last-allowed time if the key has
        never been seen or its cooldown window has fully elapsed.

        Return False (without updating the timestamp) if the request arrives
        too soon after the previous one.

        A cooldown_seconds value of 0 or less always returns True (no limit).
        """
        if cooldown_seconds <= 0:
            return True

        now = time.monotonic()
        with self._lock:
            last = self._last_seen.get(key)
            if last is None or (now - last) >= cooldown_seconds:
                self._last_seen[key] = now
                return True
            return False

    def reset(self, key: str) -> None:
        """Manually clear a cooldown entry (e.g. for testing)."""
        with self._lock:
            self._last_seen.pop(key, None)

    def reset_all(self) -> None:
        """Clear all recorded cooldowns."""
        with self._lock:
            self._last_seen.clear()
