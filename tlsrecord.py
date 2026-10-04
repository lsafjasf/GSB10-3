"""TLS 记录层(record layer)解析库 —— 仅使用 Python 3 标准库。

记录格式(RFC 5246 6.2.1):
    struct {
        ContentType type;        // 1 字节
        ProtocolVersion version; // 2 字节, major.minor
        uint16 length;           // 2 字节, fragment 长度
        opaque fragment[length];
    } TLSPlaintext;

握手消息(RFC 5246 7.1)可跨多条记录承载:
    struct {
        HandshakeType msg_type; // 1 字节
        uint24 length;          // 3 字节
        opaque body[length];
    } Handshake;
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import List, Union

RECORD_HEADER_LEN = 5
HANDSHAKE_HEADER_LEN = 4
MAX_PLAINTEXT_LENGTH = 1 << 14              # RFC 5246 规定的明文分片上限
MAX_RECORD_LENGTH = (1 << 14) + 2048        # 允许压缩/加密扩展后的记录上限
MAX_HANDSHAKE_BODY_LENGTH = (1 << 24) - 1


class ContentType(enum.IntEnum):
    """TLS 记录内容类型。"""

    CHANGE_CIPHER_SPEC = 20
    ALERT = 21
    HANDSHAKE = 22
    APPLICATION_DATA = 23


class HandshakeType(enum.IntEnum):
    """常见握手消息类型(RFC 5246/8446)。"""

    HELLO_REQUEST = 0
    CLIENT_HELLO = 1
    SERVER_HELLO = 2
    NEW_SESSION_TICKET = 4
    END_OF_EARLY_DATA = 5
    ENCRYPTED_EXTENSIONS = 8
    CERTIFICATE = 11
    SERVER_KEY_EXCHANGE = 12
    CERTIFICATE_REQUEST = 13
    SERVER_HELLO_DONE = 14
    CERTIFICATE_VERIFY = 15
    CLIENT_KEY_EXCHANGE = 16
    FINISHED = 20


# 记录层接受的版本: SSL 3.0 / TLS 1.0 / 1.1 / 1.2。
# TLS 1.3 的 legacy_record_version 也固定为 0x0303, 落在其中。
SUPPORTED_VERSIONS = frozenset({0x0300, 0x0301, 0x0302, 0x0303})

ALERT_LEVELS = {1: "warning", 2: "fatal"}
ALERT_DESCRIPTIONS = {
    0: "close_notify",
    10: "unexpected_message",
    20: "bad_record_mac",
    21: "decryption_failed",
    22: "record_overflow",
    30: "decompression_failure",
    40: "handshake_failure",
    41: "no_certificate",
    42: "bad_certificate",
    43: "unsupported_certificate",
    44: "certificate_revoked",
    45: "certificate_expired",
    46: "certificate_unknown",
    47: "illegal_parameter",
    48: "unknown_ca",
    49: "access_denied",
    50: "decode_error",
    51: "decrypt_error",
    60: "export_restriction",
    70: "protocol_version",
    71: "insufficient_security",
    80: "internal_error",
    86: "inappropriate_fallback",
    90: "user_canceled",
    100: "no_renegotiation",
    112: "missing_extension",
    120: "no_application_protocol",
}


class RecordLayerError(Exception):
    """所有记录层解析错误的基类。"""


class UnknownContentTypeError(RecordLayerError):
    """记录首字节不是任何已知内容类型。"""

    def __init__(self, type_byte: int):
        self.type_byte = type_byte
        super().__init__(f"未知记录类型: 0x{type_byte:02x}")


class UnsupportedVersionError(RecordLayerError):
    """版本字段不在接受范围内。"""

    def __init__(self, version: int):
        self.version = version
        super().__init__(
            f"不支持的记录版本: 0x{version:04x} "
            f"(major={version >> 8}, minor={version & 0xff})"
        )


class RecordOverflowError(RecordLayerError):
    """length 字段声明的长度超过协议允许的最大值。"""

    def __init__(self, declared: int, maximum: int):
        self.declared = declared
        self.maximum = maximum
        super().__init__(f"记录长度 {declared} 超过上限 {maximum}")


class NeedMoreDataError(RecordLayerError):
    """缓冲区中的字节不够, 必须由调用方继续喂数据, 不能截断处理。

    missing 即“还缺多少字节”。
    """

    def __init__(self, missing: int, context: str):
        self.missing = missing
        self.context = context
        super().__init__(f"数据不足, 还缺 {missing} 字节 ({context})")


class AlertFormatError(RecordLayerError):
    """告警记录的 fragment 无法按 level+description 成对解析。"""


@dataclass(frozen=True)
class Record:
    """一条已完整解析的 TLS 记录。"""

    content_type: ContentType
    version: int
    fragment: bytes

    @property
    def version_tuple(self):
        return self.version >> 8, self.version & 0xFF

    def __repr__(self):
        return (
            f"Record(type={self.content_type.name}, "
            f"version={self.version_tuple[0]}.{self.version_tuple[1]}, "
            f"fragment={self.fragment.hex()})"
        )


@dataclass(frozen=True)
class HandshakeMessage:
    """一条重组完成的握手消息。raw 码值原样保留。"""

    msg_type: int
    body: bytes

    @property
    def type_name(self) -> str:
        try:
            return HandshakeType(self.msg_type).name
        except ValueError:
            return f"unknown({self.msg_type})"

    @property
    def raw(self) -> bytes:
        return bytes([self.msg_type]) + len(self.body).to_bytes(3, "big") + self.body


@dataclass(frozen=True)
class Alert:
    """一条告警, level / description 保留原始码值, 未知码也不丢弃。"""

    level: int
    description: int

    @property
    def level_name(self) -> str:
        return ALERT_LEVELS.get(self.level, f"unknown({self.level})")

    @property
    def description_name(self) -> str:
        return ALERT_DESCRIPTIONS.get(self.description, f"unknown({self.description})")


@dataclass(frozen=True)
class AppData:
    """应用数据事件。"""

    data: bytes


@dataclass(frozen=True)
class ChangeCipherSpec:
    """ChangeCipherSpec 事件, 原始 fragment 保留。"""

    fragment: bytes


Event = Union[HandshakeMessage, Alert, AppData, ChangeCipherSpec]


def build_record(content_type: int, version: int, fragment: bytes) -> bytes:
    """按记录格式编码, 便于构造对拍数据/测试。"""
    if not isinstance(fragment, (bytes, bytearray)):
        raise TypeError("fragment 必须是 bytes")
    if len(fragment) > MAX_RECORD_LENGTH:
        raise RecordOverflowError(len(fragment), MAX_RECORD_LENGTH)
    return (
        bytes([content_type])
        + version.to_bytes(2, "big")
        + len(fragment).to_bytes(2, "big")
        + bytes(fragment)
    )


def build_handshake(msg_type: int, body: bytes) -> bytes:
    """编码一条握手消息(type + 3 字节长度 + body)。"""
    if len(body) > MAX_HANDSHAKE_BODY_LENGTH:
        raise RecordLayerError("握手消息体过长")
    return bytes([msg_type]) + len(body).to_bytes(3, "big") + bytes(body)


def parse_record(data, offset: int = 0):
    """严格解析单条记录。

    成功返回 (Record, next_offset)。
    数据不足时抛 NeedMoreDataError(missing=还缺字节数),
    头字段非法时抛对应 RecordLayerError 子类。
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("data 必须是 bytes/bytearray")

    if len(data) - offset < RECORD_HEADER_LEN:
        raise NeedMoreDataError(
            RECORD_HEADER_LEN - (len(data) - offset), "记录头"
        )

    type_byte = data[offset]
    version = int.from_bytes(data[offset + 1 : offset + 3], "big")
    declared_len = int.from_bytes(data[offset + 3 : offset + 5], "big")

    try:
        content_type = ContentType(type_byte)
    except ValueError:
        raise UnknownContentTypeError(type_byte) from None

    if version not in SUPPORTED_VERSIONS:
        raise UnsupportedVersionError(version)

    if declared_len > MAX_RECORD_LENGTH:
        raise RecordOverflowError(declared_len, MAX_RECORD_LENGTH)

    end = offset + RECORD_HEADER_LEN + declared_len
    if len(data) < end:
        # 关键: 报告精确缺口, 而不是按已有字节截断 fragment。
        raise NeedMoreDataError(end - len(data), f"类型 {content_type.name} 的 fragment")

    fragment = bytes(data[offset + RECORD_HEADER_LEN : end])
    return Record(content_type, version, fragment), end


def parse_records(data) -> List[Record]:
    """一次性解析缓冲区中的全部记录; 末尾残缺时抛 NeedMoreDataError。"""
    records: List[Record] = []
    offset = 0
    while offset < len(data):
        record, offset = parse_record(data, offset)
        records.append(record)
    return records


def parse_alerts(fragment: bytes) -> List[Alert]:
    """告警 fragment 为若干 (level, description) 字节对, 长度必须为偶数。"""
    if len(fragment) == 0 or len(fragment) % 2 != 0:
        raise AlertFormatError(
            f"告警 fragment 长度 {len(fragment)} 非法, 必须为正偶数(level+description)"
        )
    return [
        Alert(fragment[i], fragment[i + 1])
        for i in range(0, len(fragment), 2)
    ]


class HandshakeReassembler:
    """把分散在多条握手记录里的字节流重组成完整握手消息。

    只要喂入的字节序列相同, 无论按一条记录还是按任意边界切成多条
    记录, 重组结果都一致(对拍保证)。
    """

    def __init__(self):
        self._buf = bytearray()

    @property
    def buffered(self) -> int:
        """尚未完成重组的字节数(如半截的握手消息)。"""
        return len(self._buf)

    def feed(self, fragment: bytes) -> List[HandshakeMessage]:
        self._buf += fragment
        messages: List[HandshakeMessage] = []
        while True:
            if len(self._buf) < HANDSHAKE_HEADER_LEN:
                break
            body_len = int.from_bytes(self._buf[1:4], "big")
            if len(self._buf) < HANDSHAKE_HEADER_LEN + body_len:
                break
            msg_type = self._buf[0]
            body = bytes(
                self._buf[HANDSHAKE_HEADER_LEN : HANDSHAKE_HEADER_LEN + body_len]
            )
            messages.append(HandshakeMessage(msg_type, body))
            del self._buf[: HANDSHAKE_HEADER_LEN + body_len]
        return messages


class RecordParser:
    """增量记录解析器: 任意长度、任意时机 feed 均可。

    数据不足时不报错也不截断, 只在内部缓冲, 并通过 needed 暴露缺口。
    """

    def __init__(self):
        self._buf = bytearray()

    @property
    def buffered(self) -> int:
        return len(self._buf)

    @property
    def needed(self) -> int:
        """完成当前“正在读”的记录还缺多少字节; 没有挂起记录时为 0。"""
        if not self._buf:
            return 0
        try:
            parse_record(self._buf, 0)
        except NeedMoreDataError as exc:
            return exc.missing
        return 0  # 已可完整解析一条(下次 feed 后才会取出)

    def feed(self, data) -> List[Record]:
        self._buf += data
        records: List[Record] = []
        offset = 0
        while True:
            try:
                record, next_offset = parse_record(self._buf, offset)
            except NeedMoreDataError:
                break
            records.append(record)
            offset = next_offset
        del self._buf[:offset]
        return records


class RecordDispatcher:
    """按记录类型分发: 握手进入重组器, 告警单独归类, 其余各自成类。"""

    def __init__(self):
        self.handshakes: List[HandshakeMessage] = []
        self.alerts: List[Alert] = []
        self.app_data: List[AppData] = []
        self.change_cipher_specs: List[ChangeCipherSpec] = []
        self._reassembler = HandshakeReassembler()

    def dispatch(self, record: Record) -> List[Event]:
        if record.content_type == ContentType.HANDSHAKE:
            events = self._reassembler.feed(record.fragment)
            self.handshakes.extend(events)
        elif record.content_type == ContentType.ALERT:
            events = parse_alerts(record.fragment)
            self.alerts.extend(events)
        elif record.content_type == ContentType.APPLICATION_DATA:
            events = [AppData(record.fragment)]
            self.app_data.extend(events)
        elif record.content_type == ContentType.CHANGE_CIPHER_SPEC:
            events = [ChangeCipherSpec(record.fragment)]
            self.change_cipher_specs.extend(events)
        else:  # parse_record 已拦截, 这里防御性处理
            raise UnknownContentTypeError(int(record.content_type))
        return events

    def feed_records(self, records) -> List[Event]:
        events: List[Event] = []
        for record in records:
            events.extend(self.dispatch(record))
        return events
