"""
SpectreTTS Gateway - Speech Arbiter & Queue Manager
-----------------------------------------------------
The SpeechArbiter is the central traffic cop for all TTS requests.  It:

  1. Accepts SpeechRequest objects via `submit()`
  2. Enforces cooldown / deduplication (RateLimiter)
  3. Routes requests into a single PriorityQueue
     - LOW / AMBIENT requests are dropped when the queue backlog is ≥ 5
     - URGENT requests trigger engine.stop() (interrupt) and optionally
       drain LOW/AMBIENT items already in the queue
  4. A background worker thread pops the highest-priority item and:
       a. Calls engine.speak(text)
       b. Waits for engine.is_speaking to go False (audio done)
       c. Sleeps a configurable "breathing pause" (default 1.2 s)
       d. Logs the completion to AuditLogger
  5. Handles `stop`, `pause`, `resume`, `status`, and `history` control
     commands that bypass the queue entirely

Priority tier capacities (spec §4):
    URGENT  → 5  (always admitted; interrupt semantics)
    HIGH    → 10 (never dropped)
    NORMAL  → 25 (FIFO with pacing)
    LOW     → 5  (dropped when low-priority backlog ≥ 5)
    AMBIENT → 5  (same as LOW)
"""

import queue
import threading
import time
from typing import Any

from .models import Priority, SpeechRequest
from .audit_logger import AuditLogger
from .rate_limiter import RateLimiter


# Items at or above this numeric priority level are subject to backlog dropping.
_DROPPABLE_PRIORITY = Priority.LOW   # LOW = 3, AMBIENT = 4

# When the droppable-tier backlog hits this size, new droppable items are
# rejected to prevent an ever-growing queue of stale idle roasts.
_LOW_PRIORITY_QUEUE_CAP = 5


class SpeechArbiter:
    """
    Priority queue scheduler and worker thread for TTS requests.

    Instantiate once, call `.start()`, then feed requests via `.submit()`.
    The engine reference is only accessed from the worker thread (single
    consumer), so no extra locking around engine calls is needed.
    """

    def __init__(self, engine: Any, breathing_pause_sec: float = 1.2) -> None:
        """
        Args:
            engine: A TTSEngine instance.  Must expose:
                      .speak(text: str)
                      .stop()
                      .pause()
                      .resume()
                      .is_speaking  (bool property)
            breathing_pause_sec: Silence inserted after each utterance before
                                  the next queue item is dequeued (spec §5).
        """
        self.engine           = engine
        self.breathing_pause  = breathing_pause_sec

        # Single PriorityQueue: Python's heapq-backed thread-safe queue.
        # SpeechRequest is orderable by (priority, timestamp) — lower int first.
        self._queue: queue.PriorityQueue[SpeechRequest] = queue.PriorityQueue()

        self._logger  = AuditLogger()
        self._limiter = RateLimiter()

        self._running      = False
        self._worker: threading.Thread | None = None

        # Signals the worker to break out of its breathing-pause sleep early
        # (used when an URGENT/interrupt request arrives mid-pause).
        self._interrupt_event = threading.Event()

        # Guards _drain_lower_priorities() which temporarily empties the queue.
        self._drain_lock = threading.Lock()

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background worker thread.  Call once at daemon startup."""
        self._running = True
        self._worker  = threading.Thread(
            target  = self._process_queue,
            name    = "spectretts-arbiter",
            daemon  = True,
        )
        self._worker.start()
        print("[Gateway] Speech arbiter started.")

    def stop(self) -> None:
        """Gracefully stop the worker.  Waits up to 3 s for it to exit."""
        self._running = False
        self._interrupt_event.set()   # wake up any sleeping worker
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=3)
        print("[Gateway] Speech arbiter stopped.")

    # ── Public submission ──────────────────────────────────────────────────────

    def submit(self, req: SpeechRequest) -> bool:
        """
        Attempt to enqueue a SpeechRequest.

        Returns True  if the request was accepted (enqueued).
        Returns False if it was rejected (cooldown active or queue full).

        Side-effects for interrupt/urgent requests happen synchronously
        on the calling thread (engine.stop, queue drain) so the newly
        admitted request rises to the top of the queue before the worker
        next pops an item.
        """
        # ── Cooldown / deduplication ──────────────────────────────────────────
        if req.cooldown_key and not self._limiter.allow(req.cooldown_key, req.cooldown_seconds):
            self._logger.log_dropped(req, reason="cooldown_active")
            print(f"[Gateway] Dropped '{req.id}' ({req.source}): cooldown_active "
                  f"(key={req.cooldown_key!r})")
            return False

        # ── Low-priority backlog cap ───────────────────────────────────────────
        if req.priority >= _DROPPABLE_PRIORITY:
            low_count = sum(
                1 for item in list(self._queue.queue)   # snapshot; not ideal but safe for a small queue
                if item.priority >= _DROPPABLE_PRIORITY
            )
            if low_count >= _LOW_PRIORITY_QUEUE_CAP:
                self._logger.log_dropped(req, reason="queue_full_low_priority")
                print(f"[Gateway] Dropped '{req.id}' ({req.source}): low-priority backlog full "
                      f"({low_count}/{_LOW_PRIORITY_QUEUE_CAP})")
                return False

        # ── Interrupt / urgent handling ────────────────────────────────────────
        if req.interrupt or req.priority == Priority.URGENT:
            # Stop whatever is currently playing
            self.engine.stop()
            # Wake the worker out of its breathing pause
            self._interrupt_event.set()
            # For URGENT, also purge low-priority items from the queue
            if req.priority == Priority.URGENT:
                self._drain_lower_priorities()

        # ── Enqueue ───────────────────────────────────────────────────────────
        req.enqueue_time = time.monotonic()
        self._queue.put(req)
        print(f"[Gateway] Queued '{req.id}' priority={req.priority.name} "
              f"source={req.source!r} (qsize={self._queue.qsize()})")
        return True

    # ── Control commands (bypass queue) ───────────────────────────────────────

    def handle_stop(self) -> None:
        """Immediately stop playback and clear the queue."""
        self._interrupt_event.set()
        self.engine.stop()
        self._clear_queue()

    def handle_pause(self) -> None:
        self.engine.pause()

    def handle_resume(self) -> None:
        self.engine.resume()

    def status(self) -> dict:
        """Return a dict snapshot of the arbiter's current state."""
        return {
            "is_speaking":  self.engine.is_speaking,
            "queue_size":   self._queue.qsize(),
            "breathing_pause_sec": self.breathing_pause,
        }

    def history(self, n: int = 20) -> list[dict]:
        """Return recent audit log records."""
        return self._logger.recent(n)

    # ── Internal queue helpers ─────────────────────────────────────────────────

    def _drain_lower_priorities(self) -> None:
        """
        Remove LOW and AMBIENT items from the queue when an URGENT request
        arrives.  HIGH and above are kept.  Items removed are logged as
        'preempted_by_urgent'.

        Uses a temporary list to drain-and-refill since PriorityQueue has no
        remove-by-predicate API.  The drain lock prevents two concurrent
        urgent requests from trampling each other.
        """
        with self._drain_lock:
            kept: list[SpeechRequest] = []
            while not self._queue.empty():
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item.priority <= Priority.HIGH:
                    kept.append(item)
                else:
                    self._logger.log_dropped(item, reason="preempted_by_urgent")
                    print(f"[Gateway] Preempted '{item.id}' ({item.source}) by URGENT request.")

            for item in kept:
                self._queue.put(item)

    def _clear_queue(self) -> None:
        """Drain the entire queue (called by handle_stop)."""
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
                self._logger.log_dropped(item, reason="queue_cleared_by_stop")
            except queue.Empty:
                break

    # ── Worker thread ──────────────────────────────────────────────────────────

    def _process_queue(self) -> None:
        """
        Background worker: dequeues one request at a time, synthesises it,
        waits for playback to finish, then enforces the breathing pause before
        fetching the next item.
        """
        while self._running:
            # Block until a request arrives (0.5 s timeout so we can check
            # _running and cleanly exit when stop() is called).
            try:
                req: SpeechRequest = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            self._interrupt_event.clear()   # reset for this round

            start_wall = time.time()
            self._logger.log_start(req)
            print(f"[Gateway] Speaking '{req.id}' from {req.source!r}: "
                  f"{req.text[:60]!r}{'…' if len(req.text) > 60 else ''}")

            try:
                self.engine.speak(req.text)

                # Poll until the engine finishes (or we get interrupted)
                while self.engine.is_speaking and self._running:
                    if self._interrupt_event.is_set():
                        break
                    time.sleep(0.05)

                # Breathing pause — skippable by a new interrupt
                if self._running and not self._interrupt_event.is_set():
                    self._interrupt_event.wait(timeout=self.breathing_pause)

                duration = time.time() - start_wall
                self._logger.log_complete(req, duration=duration)

            except Exception as exc:
                self._logger.log_error(req, error=str(exc))
                print(f"[Gateway] Error while speaking '{req.id}': {exc}")
            finally:
                self._queue.task_done()
