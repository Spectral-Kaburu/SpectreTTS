"""
SpectreTTS Gateway - Data Models
----------------------------------
Defines the canonical `SpeechRequest` dataclass and `Priority` enum used
throughout the Gateway / Arbiter layer.

`SpeechRequest` is orderable (order=True on the dataclass) so Python's
`queue.PriorityQueue` can sort requests: lower `priority` integer = higher
urgency.  Within the same priority tier requests are ordered by `timestamp`
(submission time) to give true FIFO behaviour inside each tier.

`enqueue_time` is set by the caller when the request enters the queue so the
Arbiter can compute `queue_wait_ms` for the audit log.
"""

from dataclasses import dataclass, field
from enum import IntEnum
import time
import uuid


class Priority(IntEnum):
    """
    Numeric rank for speech requests.  Lower int = higher urgency.

    Maps from the JSON `priority` string field:
        "urgent"  → 0  (interrupts current speech, drains low queue)
        "high"    → 1  (played right after current sentence)
        "normal"  → 2  (FIFO with pacing, default)
        "low"     → 3  (dropped if low-priority backlog > 5)
        "ambient" → 4  (same semantics as low, separate label)
    """
    URGENT  = 0
    HIGH    = 1
    NORMAL  = 2
    LOW     = 3
    AMBIENT = 4


# Map JSON priority string → Priority enum
PRIORITY_MAP: dict[str, Priority] = {
    "urgent":  Priority.URGENT,
    "high":    Priority.HIGH,
    "normal":  Priority.NORMAL,
    "low":     Priority.LOW,
    "ambient": Priority.AMBIENT,
}


@dataclass(order=True)
class SpeechRequest:
    """
    A single speech synthesis job submitted to the Arbiter.

    The `order=True` flag means Python will auto-generate __lt__ / __le__ /
    __gt__ / __ge__ using fields in declaration order.  Fields marked
    `compare=False` are excluded from comparison so the PriorityQueue only
    sorts by (priority, timestamp) — which gives us: most urgent tier first,
    then earliest submission within that tier.
    """

    # ── Comparison fields (ordering key) ─────────────────────────────────────
    priority:          Priority = Priority.NORMAL   # primary sort key
    timestamp:         float    = field(default_factory=time.monotonic)  # secondary

    # ── Metadata (excluded from ordering) ────────────────────────────────────
    id:                str  = field(default_factory=lambda: "spk_" + uuid.uuid4().hex[:6], compare=False)
    source:            str  = field(default="unknown",  compare=False)
    category:          str  = field(default="general",  compare=False)
    text:              str  = field(default="",          compare=False)
    interrupt:         bool = field(default=False,       compare=False)
    cooldown_key:      str  = field(default="",          compare=False)
    cooldown_seconds:  int  = field(default=0,           compare=False)

    # Set by the Arbiter when the item enters the PriorityQueue so we can
    # compute queue_wait_ms in the audit log.
    enqueue_time:      float = field(default=0.0, compare=False)

    @classmethod
    def from_json(cls, payload: dict) -> "SpeechRequest":
        """
        Build a SpeechRequest from a parsed JSON command dict.

        Handles unknown / missing fields gracefully: missing priority defaults
        to "normal", unknown priority strings are also normalised to NORMAL.
        Sources "hotkey" and "urgent" default interrupt=True.
        """
        raw_priority = payload.get("priority", "normal").lower()
        priority = PRIORITY_MAP.get(raw_priority, Priority.NORMAL)

        source = payload.get("source", "unknown")
        interrupt = payload.get("interrupt", source in ("hotkey",) or priority == Priority.URGENT)

        return cls(
            priority         = priority,
            timestamp        = time.monotonic(),
            source           = source,
            category         = payload.get("category", "general"),
            text             = payload.get("text", ""),
            interrupt        = bool(interrupt),
            cooldown_key     = payload.get("cooldown_key", ""),
            cooldown_seconds = int(payload.get("cooldown_seconds", 0)),
        )

    @classmethod
    def legacy(cls, text: str) -> "SpeechRequest":
        """
        Build a SpeechRequest from a legacy pipe-delimited speak command.
        Always normal priority, no interrupt, source="legacy".
        """
        return cls(
            priority  = Priority.NORMAL,
            timestamp = time.monotonic(),
            source    = "legacy",
            category  = "general",
            text      = text,
            interrupt = False,
        )
