"""
SpectreTTS Gateway
------------------
Public re-exports for the gateway sub-package.

    from engine.gateway import SpeechArbiter, SpeechRequest, Priority
"""

from .arbiter      import SpeechArbiter
from .models       import SpeechRequest, Priority, PRIORITY_MAP
from .audit_logger import AuditLogger
from .rate_limiter import RateLimiter

__all__ = [
    "SpeechArbiter",
    "SpeechRequest",
    "Priority",
    "PRIORITY_MAP",
    "AuditLogger",
    "RateLimiter",
]
