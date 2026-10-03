"""差错报文构造与校验库（仅使用 Python 标准库）。

报文格式（所有整数均为网络字节序/大端）::

    0                   1                   2                   3
    0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
   +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
   |     Type      |     Code      |           Checksum            |
   +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
   |            Length             |   Protocol    |   Reserved    |
   +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
   |                          Payload ...                          |

- Type/Code: 差错类型与代码。
- Checksum: 对整个报文（计算时该字段置 0）做 RFC 1071 反码求和。
- Length: 含 8 字节首部在内的报文总长度。
- Protocol: 随包载荷的协议号，构造与解析双方必须一致。
"""

import struct
from collections import namedtuple

HEADER_LEN = 8

# 协议号（与 IP 协议号约定一致：1 = ICMP，此处表示差错报文载荷协议）
PROTO_ICMP = 1

MAX_PAYLOAD = 0xFFFF - HEADER_LEN


class PacketError(ValueError):
    """所有报文错误的基类。"""


class TruncatedPacketError(PacketError):
    """报文短于首部或声明长度非法。"""


class LengthMismatchError(PacketError):
    """Length 字段与实际字节数不一致。"""

    def __init__(self, declared, actual):
        self.declared = declared
        self.actual = actual
        super().__init__(
            f"长度字段与实际不符: 声明 {declared} 字节, 实际 {actual} 字节"
        )


class ProtocolMismatchError(PacketError):
    """Protocol 字段与期望协议号不一致。"""

    def __init__(self, expected, actual):
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"协议字段不匹配: 期望 0x{expected:02X}, 实际 0x{actual:02X}"
        )


class ChecksumMismatchError(PacketError):
    """校验和不匹配，同时给出期望值（按收到字节重算）与实际值（报文中携带）。"""

    def __init__(self, expected, actual):
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"校验和不匹配: 期望 0x{expected:04X}, 实际 0x{actual:04X}"
        )


Packet = namedtuple("Packet", ["type", "code", "checksum", "length",
                               "protocol", "payload"])


def ones_complement_checksum(data):
    """RFC 1071 反码求和校验和（16 位）。

    奇数长度时末尾补一个 0x00 字节；进位持续回卷直到 16 位。
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("data 必须是 bytes-like 对象")

    data = bytes(data)
    if len(data) % 2:
        data += b"\x00"

    total = 0
    for word in struct.iter_unpack("!H", data):
        total += word[0]
        # 每次相加后回卷进位，保证 total 始终不超过 17 位
        total = (total & 0xFFFF) + (total >> 16)

    total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def build_packet(msg_type, code, payload=b"", protocol=PROTO_ICMP):
    """构造差错报文：填入 Type/Code/Payload，Length/Protocol/Checksum 自动自洽。"""
    if not 0 <= msg_type <= 0xFF:
        raise ValueError("msg_type 必须在 0..255")
    if not 0 <= code <= 0xFF:
        raise ValueError("code 必须在 0..255")
    payload = bytes(payload)
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"载荷过长，最长 {MAX_PAYLOAD} 字节")

    length = HEADER_LEN + len(payload)
    header = struct.pack("!BBHHBB", msg_type, code, 0, length, protocol, 0)
    packet = header + payload

    checksum = ones_complement_checksum(packet)
    return packet[:2] + struct.pack("!H", checksum) + packet[4:]


def parse_packet(data, expected_protocol=PROTO_ICMP):
    """解析并校验差错报文。

    依次检查：截断 -> Length 自洽 -> Protocol 自洽 -> Checksum。
    任一检查失败都抛出携带具体数值的异常，而不是只返回失败标志。
    """
    data = bytes(data)
    if len(data) < HEADER_LEN:
        raise TruncatedPacketError(
            f"报文过短: 至少需要 {HEADER_LEN} 字节, 实际 {len(data)} 字节"
        )

    msg_type, code, stored_checksum, declared_length, protocol, _reserved = (
        struct.unpack("!BBHHBB", data[:HEADER_LEN])
    )

    if declared_length < HEADER_LEN:
        raise TruncatedPacketError(
            f"声明长度非法: {declared_length} 字节 (< {HEADER_LEN})"
        )
    if declared_length != len(data):
        raise LengthMismatchError(declared_length, len(data))

    if expected_protocol is not None and protocol != expected_protocol:
        raise ProtocolMismatchError(expected_protocol, protocol)

    zeroed = data[:2] + b"\x00\x00" + data[4:]
    calculated = ones_complement_checksum(zeroed)
    if calculated != stored_checksum:
        raise ChecksumMismatchError(expected=calculated,
                                    actual=stored_checksum)

    return Packet(type=msg_type, code=code, checksum=stored_checksum,
                  length=declared_length, protocol=protocol,
                  payload=data[HEADER_LEN:])
