"""TLS 风格的安全传输记录层解析器（仅标准库）。

记录帧格式（大端序，与 TLS 1.0-1.2 记录层一致）::

    struct {
        ContentType type;                 // u8
        ProtocolVersion version;         // u16  major<<8 | minor
        uint16 length;                   // 分片字节数，最大 0x4000
        opaque fragment[length];
    }

握手记录的分片内部还嵌着握手消息头::

    struct {
        HandshakeType msg_type;          // u8
        uint24 length;
        uint16 message_seq;              // RFC 5246 风格序号
        opaque body[length];
    }

一条握手消息可能横跨多条记录，解析器负责在记录边界处缓冲并重组。
"""

from .errors import (
    RecordLayerError,
    MalformedRecordError,
    TruncatedRecordError,
    TruncatedHandshakeError,
    UnknownRecordTypeError,
    InterleavedRecordError,
)
from .events import (
    Record,
    HandshakeMessage,
    Alert,
    ChangeCipherSpec,
    ApplicationData,
    UnknownRecord,
)
from .parser import (
    parse_records,
    parse_handshake_messages,
    RecordAssembler,
    iter_events,
    MAX_RECORD_LENGTH,
    SUPPORTED_VERSIONS,
    CONTENT_CHANGE_CIPHER_SPEC,
    CONTENT_ALERT,
    CONTENT_HANDSHAKE,
    CONTENT_APPLICATION_DATA,
)

__all__ = [
    "RecordLayerError",
    "MalformedRecordError",
    "TruncatedRecordError",
    "TruncatedHandshakeError",
    "UnknownRecordTypeError",
    "InterleavedRecordError",
    "Record",
    "HandshakeMessage",
    "Alert",
    "ChangeCipherSpec",
    "ApplicationData",
    "UnknownRecord",
    "parse_records",
    "parse_handshake_messages",
    "RecordAssembler",
    "iter_events",
    "MAX_RECORD_LENGTH",
    "SUPPORTED_VERSIONS",
    "CONTENT_CHANGE_CIPHER_SPEC",
    "CONTENT_ALERT",
    "CONTENT_HANDSHAKE",
    "CONTENT_APPLICATION_DATA",
]
