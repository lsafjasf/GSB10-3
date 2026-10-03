"""DNS 区域传送（AXFR over TCP）报文流解析库。

仅使用 Python 3 标准库。支持：
- 报文头、问题段、三个资源记录段的解析；
- 域名压缩指针的任意深度回跳与成环检测；
- 按 (名称, 类型) 分组输出，保持原始顺序；
- 截断 / 长度字段不符时报告已解析记录数与缺失字节数。

TCP 区域传送报文格式：[2 字节大端长度前缀][DNS 报文] ...
"""

from __future__ import annotations

import ipaddress
import struct
from dataclasses import dataclass, field
from typing import Iterator, List, Tuple

TYPE_NAMES = {
    1: "A", 2: "NS", 5: "CNAME", 6: "SOA", 12: "PTR",
    15: "MX", 16: "TXT", 28: "AAAA", 33: "SRV",
    252: "AXFR", 255: "ANY",
}

CLASS_NAMES = {1: "IN", 3: "CH", 4: "HS", 255: "ANY"}


class DNSError(Exception):
    """DNS 解析相关错误基类。"""


class NameLoopError(DNSError):
    """压缩指针回指，形成环路。"""

    def __init__(self, offset: int):
        self.offset = offset
        super().__init__(f"compression pointer loop detected at offset {offset}")


class TruncatedError(DNSError):
    """报文被截断或长度字段与实际内容不符。

    records_parsed: 出错前已完整解析的资源记录数；
    missing_bytes:  当前读取位置还缺多少字节。
    """

    def __init__(self, detail: str, records_parsed: int = 0, missing_bytes: int = 0):
        self.detail = detail
        self.records_parsed = records_parsed
        self.missing_bytes = missing_bytes
        super().__init__(
            f"{detail} (records parsed: {records_parsed}, "
            f"missing bytes: {missing_bytes})"
        )


def _type_name(code: int) -> str:
    return TYPE_NAMES.get(code, f"TYPE{code}")


def _class_name(code: int) -> str:
    return CLASS_NAMES.get(code, f"CLASS{code}")


def parse_name(data: bytes, offset: int) -> Tuple[str, int]:
    """解析一个域名（可能含压缩指针），返回 (域名, 该字段之后的绝对偏移)。

    遇到压缩指针时返回的偏移是当前字段占用字节之后的位置，而不是被指
    向数据的末尾。指针可以指向任意偏移（任意深度回跳）；若再次跳到曾
    经的指针目标则抛出 NameLoopError。
    """
    labels: List[str] = []
    jumped = False
    end_offset = 0
    pos = offset
    seen_targets = set()

    while True:
        if pos >= len(data):
            raise TruncatedError(
                "domain name truncated",
                missing_bytes=pos - len(data) + 1,
            )
        length = data[pos]
        tag = length & 0xC0
        if tag == 0xC0:  # 压缩指针
            if pos + 1 >= len(data):
                raise TruncatedError(
                    "compression pointer truncated",
                    missing_bytes=pos + 2 - len(data),
                )
            target = ((length & 0x3F) << 8) | data[pos + 1]
            if target in seen_targets:
                raise NameLoopError(target)
            seen_targets.add(target)
            if not jumped:
                end_offset = pos + 2
                jumped = True
            pos = target
            continue
        if tag != 0x00:  # 不支持 0x40(扩展标签) / 0x80
            raise DNSError(f"unsupported label type 0x{tag:02x} at offset {pos}")
        pos += 1
        if length == 0:
            if not jumped:
                end_offset = pos
            break
        if pos + length > len(data):
            raise TruncatedError(
                "label content truncated",
                missing_bytes=pos + length - len(data),
            )
        labels.append(data[pos:pos + length].decode("ascii", "backslashreplace"))
        pos += length

    return (".".join(labels) if labels else "."), end_offset


@dataclass
class Header:
    id: int
    flags: int
    qr: int
    opcode: int
    aa: int
    tc: int
    rd: int
    ra: int
    rcode: int
    qdcount: int
    ancount: int
    nscount: int
    arcount: int

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "flags": f"0x{self.flags:04x}",
            "qr": self.qr,
            "opcode": self.opcode,
            "aa": self.aa,
            "tc": self.tc,
            "rd": self.rd,
            "ra": self.ra,
            "rcode": self.rcode,
            "qdcount": self.qdcount,
            "ancount": self.ancount,
            "nscount": self.nscount,
            "arcount": self.arcount,
        }


@dataclass
class Question:
    name: str
    qtype: int
    qclass: int

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": _type_name(self.qtype),
            "class": _class_name(self.qclass),
        }


@dataclass
class Record:
    name: str
    rtype: int
    rclass: int
    ttl: int
    rdata: str
    section: str

    @property
    def type_name(self) -> str:
        return _type_name(self.rtype)

    def to_dict(self) -> dict:
        return {
            "section": self.section,
            "name": self.name,
            "type": self.type_name,
            "class": _class_name(self.rclass),
            "ttl": self.ttl,
            "rdata": self.rdata,
        }


@dataclass
class Message:
    header: Header
    questions: List[Question] = field(default_factory=list)
    answers: List[Record] = field(default_factory=list)
    authorities: List[Record] = field(default_factory=list)
    additionals: List[Record] = field(default_factory=list)

    def all_records(self) -> Iterator[Record]:
        yield from self.answers
        yield from self.authorities
        yield from self.additionals

    @property
    def record_count(self) -> int:
        return len(self.answers) + len(self.authorities) + len(self.additionals)


def _need(data: bytes, offset: int, size: int, what: str) -> None:
    if len(data) - offset < size:
        raise TruncatedError(
            f"{what} truncated",
            missing_bytes=size - (len(data) - offset),
        )


def _decode_rdata_name(data: bytes, offset: int) -> Tuple[str, int]:
    return parse_name(data, offset)


def decode_rdata(data: bytes, offset: int, rtype: int, rdlength: int) -> str:
    """把常见类型的 RDATA 解码成可读字符串；未知类型输出 hex。"""
    end = offset + rdlength
    if rtype in (1,):
        if rdlength == 4:
            return str(ipaddress.IPv4Address(data[offset:end]))
    elif rtype == 28:
        if rdlength == 16:
            return str(ipaddress.IPv6Address(data[offset:end]))
    elif rtype in (2, 5, 12):  # NS / CNAME / PTR
        name, _ = _decode_rdata_name(data, offset)
        return name
    elif rtype == 15:  # MX
        if rdlength >= 3:
            pref = struct.unpack_from("!H", data, offset)[0]
            name, _ = parse_name(data, offset + 2)
            return f"{pref} {name}"
    elif rtype == 33:  # SRV
        if rdlength >= 7:
            priority, weight, port = struct.unpack_from("!HHH", data, offset)
            name, _ = parse_name(data, offset + 6)
            return f"{priority} {weight} {port} {name}"
    elif rtype == 6:  # SOA
        mname, pos = parse_name(data, offset)
        rname, pos = parse_name(data, pos)
        if len(data) - pos < 20:
            raise TruncatedError(
                "SOA rdata truncated",
                missing_bytes=20 - (len(data) - pos),
            )
        serial, refresh, retry, expire, minimum = struct.unpack_from("!IIIII", data, pos)
        return (f"{mname} {rname} {serial} {refresh} {retry} {expire} {minimum}")
    elif rtype == 16:  # TXT
        pieces = []
        pos = offset
        while pos < end:
            slen = data[pos]
            pos += 1
            if pos + slen > end:
                raise TruncatedError(
                    "TXT rdata truncated",
                    missing_bytes=pos + slen - end,
                )
            pieces.append(data[pos:pos + slen].decode("ascii", "backslashreplace"))
            pos += slen
        return " ".join(pieces)
    return data[offset:end].hex()


def parse_record(data: bytes, offset: int, section: str) -> Tuple[Record, int]:
    name, offset = parse_name(data, offset)
    _need(data, offset, 10, "record header")
    rtype, rclass, ttl, rdlength = struct.unpack_from("!HHIH", data, offset)
    offset += 10
    _need(data, offset, rdlength, "record rdata")
    rdata = decode_rdata(data, offset, rtype, rdlength)
    offset += rdlength
    return Record(name, rtype, rclass, ttl, rdata, section), offset


def parse_message(data: bytes) -> Message:
    """解析单个完整 DNS 报文（不含 TCP 长度前缀）。"""
    _need(data, 0, 12, "message header")
    ident, flags, qd, an, ns, ar = struct.unpack_from("!HHHHHH", data, 0)
    header = Header(
        id=ident,
        flags=flags,
        qr=(flags >> 15) & 1,
        opcode=(flags >> 11) & 0xF,
        aa=(flags >> 10) & 1,
        tc=(flags >> 9) & 1,
        rd=(flags >> 8) & 1,
        ra=(flags >> 7) & 1,
        rcode=flags & 0xF,
        qdcount=qd, ancount=an, nscount=ns, arcount=ar,
    )

    offset = 12
    questions: List[Question] = []
    for _ in range(qd):
        qname, offset = parse_name(data, offset)
        _need(data, offset, 4, "question")
        qtype, qclass = struct.unpack_from("!HH", data, offset)
        offset += 4
        questions.append(Question(qname, qtype, qclass))

    answers: List[Record] = []
    authorities: List[Record] = []
    additionals: List[Record] = []
    parsed = 0
    for section, count, out in (
        ("answer", an, answers),
        ("authority", ns, authorities),
        ("additional", ar, additionals),
    ):
        for _ in range(count):
            try:
                record, offset = parse_record(data, offset, section)
            except TruncatedError as exc:
                exc.records_parsed = parsed
                raise
            out.append(record)
            parsed += 1

    return Message(header, questions, answers, authorities, additionals)


def parse_stream(stream: bytes) -> List[Message]:
    """解析 TCP 区域传送流：多个 [2 字节长度][DNS 报文] 帧顺序拼接。"""
    messages: List[Message] = []
    records_before = 0
    offset = 0
    while offset < len(stream):
        remaining = len(stream) - offset
        if remaining < 2:
            raise TruncatedError(
                "stream frame length prefix truncated",
                records_parsed=records_before,
                missing_bytes=2 - remaining,
            )
        (frame_len,) = struct.unpack_from("!H", stream, offset)
        offset += 2
        remaining = len(stream) - offset
        if remaining < frame_len:
            raise TruncatedError(
                f"frame declares {frame_len} bytes but only {remaining} available",
                records_parsed=records_before,
                missing_bytes=frame_len - remaining,
            )
        try:
            message = parse_message(stream[offset:offset + frame_len])
        except TruncatedError as exc:
            exc.records_parsed += records_before
            raise
        messages.append(message)
        records_before += message.record_count
        offset += frame_len
    return messages


def group_records(messages: List[Message]) -> List[dict]:
    """按 (名称, 类型) 分组；组按首次出现顺序排列，组内保持原始顺序。"""
    groups: List[dict] = []
    index = {}
    for message in messages:
        for record in message.all_records():
            key = (record.name, record.type_name)
            group = index.get(key)
            if group is None:
                group = {"name": record.name, "type": record.type_name, "records": []}
                index[key] = group
                groups.append(group)
            group["records"].append(record.to_dict())
    return groups
