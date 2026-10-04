"""Index bloat detection and online rebuild (stdlib only)."""

from .log_index import LogIndex
from .metrics import (
    BloatReport,
    MIN_REBUILD_BYTES,
    REBUILD_EFFICIENCY_THRESHOLD,
    measure,
)
from .rebuilder import (
    OnlineRebuilder,
    RebuildInterrupted,
    RebuildVerificationFailed,
    differential_check,
)

__all__ = [
    "LogIndex",
    "BloatReport",
    "MIN_REBUILD_BYTES",
    "REBUILD_EFFICIENCY_THRESHOLD",
    "measure",
    "OnlineRebuilder",
    "RebuildInterrupted",
    "RebuildVerificationFailed",
    "differential_check",
]
