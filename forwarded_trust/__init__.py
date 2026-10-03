"""forwarded_trust：反向代理后的来源头部解析与信任判定。"""

from .core import (
    ChainEntry,
    ParseResult,
    Resolution,
    parse_forwarded,
    parse_x_forwarded_for,
    resolve_client,
)

__all__ = [
    "ChainEntry",
    "ParseResult",
    "Resolution",
    "parse_forwarded",
    "parse_x_forwarded_for",
    "resolve_client",
]
