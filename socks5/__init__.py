"""SOCKS5 客户端握手库（仅标准库）。"""
from .reply import (
    ATYP_IPV4,
    ATYP_DOMAIN,
    ATYP_IPV6,
    REP_MESSAGES,
    Reply,
    ReplyParseError,
    TruncatedReply,
    UnknownAddressType,
    UnknownReplyCode,
    parse_reply,
)
from .client import Socks5Client, Socks5Error, NegotiationError

__all__ = [
    "ATYP_IPV4",
    "ATYP_DOMAIN",
    "ATYP_IPV6",
    "REP_MESSAGES",
    "Reply",
    "ReplyParseError",
    "TruncatedReply",
    "UnknownAddressType",
    "UnknownReplyCode",
    "parse_reply",
    "Socks5Client",
    "Socks5Error",
    "NegotiationError",
]
