# -*- coding: utf-8 -*-
"""ICMP 风格差错报文的构造、解析与校验（仅使用标准库）。

报文布局（8 字节定长头 + 随包数据）::

     0                   1                   2                   3
     0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    |     类型      |     代码      |           校验和              |
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    |            长度（含头，单位字节）             |    协议 | 保留 |
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    |                         随包数据 ...                          |
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

- 校验和：RFC 1071 互联网校验和，对整份报文（校验和字段先置零）
  按 16 位字反码求和后取反；奇数长度时末尾补一个零字节。
- 长度字段：整份报文（头 + 随包数据）的字节数，构造与解析都强制自洽。
- 协议字段：随包数据对应报文的协议号（1=ICMP, 6=TCP, 17=UDP）。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

HEADER_LEN = 8
MAX_MESSAGE_LEN = 0xFFFF

# 常用 IP 协议号（与 RFC 790 / IANA 一致）
PROTO_ICMP = 1
PROTO_TCP = 6
PROTO_UDP = 17
KNOWN_PROTOCOLS = {PROTO_ICMP: "ICMP", PROTO_TCP: "TCP", PROTO_UDP: "UDP"}


class MessageError(ValueError):
    """报文构造或解析相关错误的基类。"""


class ChecksumMismatchError(MessageError):
    """解析时校验和不匹配，同时携带报文声明值与按内容重算值。"""

    def __init__(self, expected: int, actual: int):
        self.expected = expected  # 报文校验和字段中声明的值
        self.actual = actual      # 按当前内容重新计算得到的值
        super().__init__(
            "校验和不匹配：报文声明 0x%04x，按内容重算为 0x%04x"
            % (expected, actual)
        )


class LengthMismatchError(MessageError):
    """声明长度与实际字节数不一致。"""

    def __init__(self, declared: int, actual: int):
        self.declared = declared
        self.actual = actual
        super().__init__(
            "长度字段声明 %d 字节，实际收到 %d 字节" % (declared, actual)
        )


class ProtocolError(MessageError):
    """协议字段取值非法。"""


@dataclass(frozen=True)
class ErrorMessage:
    """解析成功后的差错报文。"""

    type: int
    code: int
    checksum: int
    length: int
    protocol: int
    payload: bytes

    @property
    def protocol_name(self) -> str:
        return KNOWN_PROTOCOLS.get(self.protocol, "未知协议")


def _check_byte(name: str, value: int) -> None:
    if not isinstance(value, int):
        raise MessageError("%s 必须是整数" % name)
    if not 0 <= value <= 0xFF:
        raise MessageError("%s=%d 超出单字节范围 [0,255]" % (name, value))


def internet_checksum(data: bytes) -> int:
    """RFC 1071 互联网校验和：16 位字反码求和后取反。

    奇数长度时在末尾补一个零字节凑成整字（RFC 1071 §2）。
    返回 0..65535 的 16 位校验和。
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("data 必须是 bytes-like 对象")
    data = bytes(data)
    if len(data) & 1:
        data += b"\x00"

    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) | data[i + 1]
        # 带回绕进位，避免 total 无限增长
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def verify_checksum(data: bytes) -> bool:
    """对一份校验和字段已填好的报文做整体校验。

    校验正确时，整份报文（含校验和）反码求和结果为 0xFFFF（即 -0）。
    """
    return internet_checksum(data) == 0


def build_error_message(
    msg_type: int,
    code: int,
    payload: bytes = b"",
    *,
    protocol: int = PROTO_ICMP,
) -> bytes:
    """按字段构造差错报文，自动填入自洽的校验和与长度。

    参数:
        msg_type: 类型字段（0..255）。
        code:     代码字段（0..255）。
        payload:  随包数据（原触发报文的头部等）。
        protocol: 随包数据的协议号，默认 ICMP(1)。
    返回:
        完整报文的字节串，长度 = 8 + len(payload)。
    """
    _check_byte("type", msg_type)
    _check_byte("code", code)
    _check_byte("protocol", protocol)
    if protocol not in KNOWN_PROTOCOLS:
        # 单字节合法但不是本工具认可的协议号
        raise ProtocolError("不支持的协议号 %d" % protocol)
    payload = bytes(payload)
    if len(payload) > MAX_MESSAGE_LEN - HEADER_LEN:
        raise MessageError("随包数据过长，超出 16 位长度字段范围")

    total_length = HEADER_LEN + len(payload)
    # 校验和字段先置零再整体求和
    header = struct.pack(
        "!BBHHBB", msg_type, code, 0, total_length, protocol, 0
    )
    checksum = internet_checksum(header + payload)
    return (
        struct.pack(
            "!BBHHBB", msg_type, code, checksum, total_length, protocol, 0
        )
        + payload
    )


def parse_error_message(data: bytes) -> ErrorMessage:
    """解析并严格校验差错报文。

    - 不足 8 字节或长度字段与实际不符 -> LengthMismatchError
    - 校验和不匹配 -> ChecksumMismatchError（含 expected/actual）
    - 协议号不被认可 -> ProtocolError
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("data 必须是 bytes-like 对象")
    data = bytes(data)
    if len(data) < HEADER_LEN:
        raise LengthMismatchError(declared=HEADER_LEN, actual=len(data))

    msg_type, code, checksum, declared_len, protocol, _reserved = (
        struct.unpack("!BBHHBB", data[:HEADER_LEN])
    )

    if declared_len != len(data):
        raise LengthMismatchError(declared=declared_len, actual=len(data))

    # 将校验和字段置零后重算，保证字节序处理与构造端一致
    zeroed = data[:2] + b"\x00\x00" + data[4:]
    actual = internet_checksum(zeroed)
    if actual != checksum:
        raise ChecksumMismatchError(expected=checksum, actual=actual)

    if protocol not in KNOWN_PROTOCOLS:
        raise ProtocolError("不支持的协议号 %d" % protocol)

    return ErrorMessage(
        type=msg_type,
        code=code,
        checksum=checksum,
        length=declared_len,
        protocol=protocol,
        payload=data[HEADER_LEN:],
    )
