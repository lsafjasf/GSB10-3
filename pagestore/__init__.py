"""Paged key/value store with crash-tolerant index rebuilding.

Modules:
    format   - on-disk page format and self-checking page codec
    store    - forward-only writer used to produce store files
    rebuild  - read-only page scan, conflict resolution and RebuiltIndex
"""

from .format import (
    MAGIC,
    HEADER_SIZE,
    encode_page,
    encode_records,
    decode_records,
    decode_page,
    PageError,
)
from .store import StoreWriter
from .rebuild import (
    RebuiltIndex,
    IndexEntry,
    Occurrence,
    Conflict,
    CorruptRegion,
    ValidPage,
    RebuildResult,
    scan_file,
    rebuild_index,
)

__all__ = [
    "MAGIC",
    "HEADER_SIZE",
    "encode_page",
    "encode_records",
    "decode_records",
    "decode_page",
    "PageError",
    "StoreWriter",
    "RebuiltIndex",
    "IndexEntry",
    "Occurrence",
    "Conflict",
    "CorruptRegion",
    "ValidPage",
    "RebuildResult",
    "scan_file",
    "rebuild_index",
]
