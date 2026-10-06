"""
SpectreTTS Gateway - Central Audit Logger
------------------------------------------
Every speech event processed by the Arbiter is appended as a single JSON
line to:

    ~/.blackboxx/tts/voice_history.jsonl

Rotation: 10 MB max, up to 3 archives (voice_history.1.jsonl, .2.jsonl, .3.jsonl).

Log record schema:
    {
        "timestamp":    "<ISO-8601 UTC>",
        "event_id":     "spk_8f9a2b",
        "source":       "annoying-sister",
        "category":     "idle_roast",
        "priority":     "low",
        "text":         "15 minutes idle and watching VLC.",
        "status":       "completed" | "started" | "error" | "dropped",
        "duration_ms":  3420,          # null on start / dropped records
        "queue_wait_ms": 45,           # ms from enqueue_time to speech start
        "interrupted":  false,
        "drop_reason":  null           # populated for status="dropped"
    }

Thread-safe: Python's `logging` module serialises writes internally;
`RotatingFileHandler` is already thread-safe.  We additionally gate on a
`threading.Lock` to keep multi-field JSON records atomic.
"""

import json
import logging
import logging.handlers
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .models import SpeechRequest


# ── Helpers ────────────────────────────────────────────────────────────────────

def _log_dir() -> Path:
    """Return (and create) the audit log directory."""
    path = Path.home() / ".blackboxx" / "tts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ── Logger class ───────────────────────────────────────────────────────────────

class AuditLogger:
    """
    Appends structured JSONL records to the rotating audit log file.

    One instance is shared by the SpeechArbiter.  All public methods are
    safe to call from the arbiter's worker thread.
    """

    MAX_BYTES  = 10 * 1024 * 1024   # 10 MB per file
    BACKUP_COUNT = 3                 # voice_history.1.jsonl … .3.jsonl

    def __init__(self) -> None:
        self._lock = threading.Lock()
        log_path = _log_dir() / "voice_history.jsonl"

        # Use a dedicated Python logger so we get RotatingFileHandler for free.
        # The logger name is unique to this class so it never clashes with the
        # root logger or any other module's logger.
        self._logger = logging.getLogger("spectretts.audit")
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False   # don't bubble up to the root logger

        if not self._logger.handlers:
            handler = logging.handlers.RotatingFileHandler(
                filename    = log_path,
                maxBytes    = self.MAX_BYTES,
                backupCount = self.BACKUP_COUNT,
                encoding    = "utf-8",
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)

    # ── Public logging methods ─────────────────────────────────────────────────

    def log_start(self, req: SpeechRequest) -> None:
        """Record that a request has left the queue and is being synthesised."""
        self._write({
            "timestamp":     _iso_now(),
            "event_id":      req.id,
            "source":        req.source,
            "category":      req.category,
            "priority":      req.priority.name.lower(),
            "text":          req.text,
            "status":        "started",
            "duration_ms":   None,
            "queue_wait_ms": self._wait_ms(req),
            "interrupted":   req.interrupt,
            "drop_reason":   None,
        })

    def log_complete(self, req: SpeechRequest, duration: float) -> None:
        """Record successful playback completion."""
        self._write({
            "timestamp":     _iso_now(),
            "event_id":      req.id,
            "source":        req.source,
            "category":      req.category,
            "priority":      req.priority.name.lower(),
            "text":          req.text,
            "status":        "completed",
            "duration_ms":   round(duration * 1000),
            "queue_wait_ms": self._wait_ms(req),
            "interrupted":   req.interrupt,
            "drop_reason":   None,
        })

    def log_error(self, req: SpeechRequest, error: str) -> None:
        """Record a synthesis / playback error."""
        self._write({
            "timestamp":     _iso_now(),
            "event_id":      req.id,
            "source":        req.source,
            "category":      req.category,
            "priority":      req.priority.name.lower(),
            "text":          req.text,
            "status":        "error",
            "duration_ms":   None,
            "queue_wait_ms": self._wait_ms(req),
            "interrupted":   req.interrupt,
            "drop_reason":   error,
        })

    def log_dropped(self, req: SpeechRequest, reason: str) -> None:
        """Record a request that was rejected before reaching the engine."""
        self._write({
            "timestamp":     _iso_now(),
            "event_id":      req.id,
            "source":        req.source,
            "category":      req.category,
            "priority":      req.priority.name.lower(),
            "text":          req.text,
            "status":        "dropped",
            "duration_ms":   None,
            "queue_wait_ms": None,
            "interrupted":   req.interrupt,
            "drop_reason":   reason,
        })

    # ── Query helpers ──────────────────────────────────────────────────────────

    def recent(self, n: int = 20) -> list[dict]:
        """
        Return the last `n` log records (across all rotation files) as a list
        of dicts, newest-last.  Used by the `history` socket command.
        Reads directly from disk — not cached — so callers shouldn't hammer it.
        """
        log_dir = _log_dir()
        lines: list[str] = []

        # Archives are numbered oldest-first, so read them in reverse then
        # append the live file last so the combined stream is chronological.
        archive_paths = sorted(
            log_dir.glob("voice_history.*.jsonl"),
            reverse=True,
        )
        live_path = log_dir / "voice_history.jsonl"

        for path in [*archive_paths, live_path]:
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        lines.extend(fh.readlines())
                except OSError:
                    pass

        records = []
        for line in lines[-n:]:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return records

    # ── Internal ───────────────────────────────────────────────────────────────

    def _write(self, record: dict) -> None:
        with self._lock:
            self._logger.info(json.dumps(record, ensure_ascii=False))

    @staticmethod
    def _wait_ms(req: SpeechRequest) -> int | None:
        if req.enqueue_time <= 0:
            return None
        return round((time.monotonic() - req.enqueue_time) * 1000)
