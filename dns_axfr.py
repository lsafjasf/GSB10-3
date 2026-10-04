"""DNS 区域传送（AXFR over TCP）响应流解析库，仅依赖标准库。

功能：
- 解析 TCP 流（2 字节长度前缀 + DNS 报文）或单个报文；
- 解析报文头、问题段、资源记录（回答/授权/附加段）；
- 域名压缩指针支持任意深度回跳，并检测指针环；
- 记录按 (名称, 类型) 分组，同组内保持报文中的原始顺序；
- 报文截断或长度前缀与实际内容不符时，抛出携带
  「已解析记录数」与「缺失字节数」的 TruncatedError。
"""

from __future__ import annotations

import ipaddress
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------- 异常

class DnsError(Exception):
    """解析错误基类。"""


class NameLoopError(DnsError):
    """域名压缩指针成环。"""

    def __init__(self, offset: int, chain: List[int]):
        self.offset = offset
        self.chain = list(chain)
        super().__init__(
            "compression pointer loop detected at offset %d (chain: %s)"
            % (offset, " -> ".join("0x%x" % c for c in chain))
        )


class TruncatedError(DnsError):
    """报文被截断或长度字段与内容不符。

    records:       出错前已成功解析的资源记录（保持原始顺序）。
    missing_bytes: 继续解析至少还需要的字节数。
    """

    def __init__(self, missing_bytes: int, records: Optional[list] = None,
                 detail: str = ""):
        self.missing_bytes = missing_bytes
        self.records: list = list(records) if records else []
        msg = "message truncated: %d more byte(s) needed, %d record(s) parsed" % (
            missing_bytes, len(self.records))
        if detail:
            msg += " (%s)" % detail
        super().__init__(msg)


# ---------------------------------------------------------------- 常量

TYPE_NAMES = {
    1: "A", 2: "NS", 3: "MD", 4: "MF", 5: "CNAME", 6: "SOA", 7: "MB",
    8: "MG", 9: "MR", 10: "NULL", 11: "WKS", 12: "PTR", 13: "HINFO",
    14: "MINFO", 15: "MX", 16: "TXT", 28: "AAAA", 33: "SRV", 35: "NAPTR",
    39: "DNAME", 41: "OPT", 43: "DS", 46: "RRSIG", 47: "NSEC", 48: "DNSKEY",
    50: "NSEC3", 51: "NSEC3PARAM", 52: "TLSA", 99: "SPF", 108: "EUI48",
    109: "EUI64", 249: "TKEY", 250: "TSIG", 251: "IXFR", 252: "AXFR",
    255: "ANY", 257: "CAA",
}

CLASS_NAMES = {1: "IN", 2: "CS", 3: "CH", 4: "HS", 255: "ANY"}

OPCODES = {0: "QUERY", 1: "IQUERY", 2: "STATUS", 4: "NOTIFY", 5: "UPDATE"}

RCODES = {
    0: "NOERROR", 1: "FORMERR", 2: "SERVFAIL", 3: "NXDOMAIN",
    4: "NOTIMP", 5: "REFUSED", 6: "YXDOMAIN", 7: "YXRRSET",
    8: "NXRRSET", 9: "NOTAUTH", 10: "NOTZONE",
}


def type_name(type_code: int) -> str:
    return TYPE_NAMES.get(type_code, "TYPE%d" % type_code)


def class_name(class_code: int) -> str:
    return CLASS_NAMES.get(class_code, "CLASS%d" % class_code)


# ---------------------------------------------------------------- 工具

def _need(data: bytes, offset: int, count: int, what: str) -> None:
    """确认 data 从 offset 起至少有 count 字节，否则抛 TruncatedError。"""
    available = len(data) - offset
    if available < count:
        raise TruncatedError(count - available, detail="reading %s" % what)


# ---------------------------------------------------------------- 域名解析

def parse_name(data: bytes, offset: int) -> Tuple[str, int]:
    """解析（可能被压缩的）域名。

    返回 (域名文本, 域名结束后下一个字节的偏移)。
    压缩指针可任意深度回跳；访问过的偏移被记录，一旦重复即成环，
    抛出 NameLoopError。
    """
    labels: List[str] = []
    visited = set()
    pos = offset
    next_offset: Optional[int] = None  # 第一次跟随指针前的「下一个偏移」

    while True:
        if pos in visited:
            raise NameLoopError(pos, sorted(visited))
        visited.add(pos)
        _need(data, pos, 1, "label length")
        length = data[pos]

        if length & 0xC0 == 0xC0:  # 压缩指针
            _need(data, pos, 2, "compression pointer")
            pointer = ((length & 0x3F) << 8) | data[pos + 1]
            if pointer >= len(data):
                raise TruncatedError(pointer + 1 - len(data),
                                     detail="pointer target 0x%x out of range"
                                            % pointer)
            if next_offset is None:
                next_offset = pos + 2
            pos = pointer
        elif length & 0xC0:
            raise DnsError("reserved label type 0x%02x at offset %d"
                           % (length, pos))
        elif length == 0:  # 根标签，结束
            if next_offset is None:
                next_offset = pos + 1
            break
        else:  # 普通标签
            _need(data, pos + 1, length, "label data")
            label = data[pos + 1: pos + 1 + length]
            labels.append(label.decode("ascii", errors="backslashreplace"))
            pos = pos + 1 + length

    name = ".".join(labels) + "." if labels else "."
    return name, next_offset


# ---------------------------------------------------------------- RDATA

# RDATA 内含域名的类型，解析时同样走 parse_name（支持压缩指针）。
def _parse_rdata(data: bytes, rdata_offset: int, rdlength: int,
                 type_code: int) -> str:
    end = rdata_offset + rdlength
    rd = data[rdata_offset:end]

    if type_code == 1 and rdlength == 4:                      # A
        return str(ipaddress.IPv4Address(rd))
    if type_code == 28 and rdlength == 16:                    # AAAA
        return str(ipaddress.IPv6Address(rd))
    if type_code in (2, 5, 12, 39):                           # NS/CNAME/PTR/DNAME
        name, _ = parse_name(data, rdata_offset)
        return name
    if type_code == 6:                                        # SOA
        mname, p = parse_name(data, rdata_offset)
        rname, p = parse_name(data, p)
        _need(data, p, 20, "SOA timers")
        serial, refresh, retry, expire, minimum = struct.unpack_from("!IIIII", data, p)
        return "%s %s %d %d %d %d %d" % (
            mname, rname, serial, refresh, retry, expire, minimum)
    if type_code == 15:                                       # MX
        _need(data, rdata_offset, 2, "MX preference")
        (pref,) = struct.unpack_from("!H", data, rdata_offset)
        name, _ = parse_name(data, rdata_offset + 2)
        return "%d %s" % (pref, name)
    if type_code == 33:                                       # SRV
        _need(data, rdata_offset, 6, "SRV fields")
        prio, weight, port = struct.unpack_from("!HHH", data, rdata_offset)
        name, _ = parse_name(data, rdata_offset + 6)
        return "%d %d %d %s" % (prio, weight, port, name)
    if type_code == 16:                                       # TXT
        parts = []
        p = rdata_offset
        while p < end:
            ln = data[p]
            parts.append(data[p + 1: p + 1 + ln].decode("ascii", "backslashreplace"))
            p += 1 + ln
        return " ".join('"%s"' % s for s in parts)
    return rd.hex()                                           # 未知类型：十六进制


# ---------------------------------------------------------------- 数据结构

@dataclass
class Record:
    name: str
    type_code: int
    class_code: int
    ttl: int
    rdata: str
    section: str = ""

    @property
    def type(self) -> str:
        return type_name(self.type_code)

    @property
    def rclass(self) -> str:
        return class_name(self.class_code)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "type": self.type, "class": self.rclass,
            "ttl": self.ttl, "rdata": self.rdata, "section": self.section,
        }


@dataclass
class Question:
    qname: str
    qtype_code: int
    qclass_code: int

    @property
    def qtype(self) -> str:
        return type_name(self.qtype_code)

    def to_dict(self) -> dict:
        return {"qname": self.qname, "qtype": self.qtype,
                "qclass": class_name(self.qclass_code)}


@dataclass
class Header:
    msg_id: int
    qr: int
    opcode: int
    aa: bool
    tc: bool
    rd: bool
    ra: bool
    rcode: int
    qdcount: int
    ancount: int
    nscount: int
    arcount: int

    def to_dict(self) -> dict:
        return {
            "id": self.msg_id, "qr": self.qr,
            "opcode": OPCODES.get(self.opcode, str(self.opcode)),
            "aa": self.aa, "tc": self.tc, "rd": self.rd, "ra": self.ra,
            "rcode": RCODES.get(self.rcode, str(self.rcode)),
            "qdcount": self.qdcount, "ancount": self.ancount,
            "nscount": self.nscount, "arcount": self.arcount,
        }


@dataclass
class Message:
    header: Header
    questions: List[Question] = field(default_factory=list)
    answers: List[Record] = field(default_factory=list)
    authorities: List[Record] = field(default_factory=list)
    additionals: List[Record] = field(default_factory=list)

    def all_records(self) -> List[Record]:
        return self.answers + self.authorities + self.additionals

    def to_dict(self) -> dict:
        return {
            "header": self.header.to_dict(),
            "questions": [q.to_dict() for q in self.questions],
            "records": [r.to_dict() for r in self.all_records()],
        }


# ---------------------------------------------------------------- 报文解析

def _parse_header(data: bytes) -> Header:
    _need(data, 0, 12, "header")
    (msg_id, flags, qd, an, ns, ar) = struct.unpack_from("!HHHHHH", data, 0)
    return Header(
        msg_id=msg_id,
        qr=(flags >> 15) & 1,
        opcode=(flags >> 11) & 0xF,
        aa=bool(flags & 0x0400),
        tc=bool(flags & 0x0200),
        rd=bool(flags & 0x0100),
        ra=bool(flags & 0x0080),
        rcode=flags & 0xF,
        qdcount=qd, ancount=an, nscount=ns, arcount=ar,
    )


def _parse_question(data: bytes, offset: int) -> Tuple[Question, int]:
    qname, offset = parse_name(data, offset)
    _need(data, offset, 4, "question qtype/qclass")
    qtype, qclass = struct.unpack_from("!HH", data, offset)
    return Question(qname, qtype, qclass), offset + 4


def _parse_record(data: bytes, offset: int, section: str) -> Tuple[Record, int]:
    name, offset = parse_name(data, offset)
    _need(data, offset, 10, "record header")
    type_code, class_code, ttl, rdlength = struct.unpack_from("!HHIH", data, offset)
    rdata_offset = offset + 10
    _need(data, rdata_offset, rdlength, "rdata of %s %s" % (name, type_name(type_code)))
    rdata = _parse_rdata(data, rdata_offset, rdlength, type_code)
    rec = Record(name, type_code, class_code, ttl, rdata, section)
    return rec, rdata_offset + rdlength


def parse_message(data: bytes) -> Message:
    """解析单个 DNS 报文。

    截断时抛出 TruncatedError，其中 records 为已解析出的记录、
    missing_bytes 为至少还缺的字节数。
    """
    header = _parse_header(data)
    msg = Message(header)
    parsed: List[Record] = []
    try:
        offset = 12
        for _ in range(header.qdcount):
            question, offset = _parse_question(data, offset)
            msg.questions.append(question)
        for section, count, sink in (
            ("answer", header.ancount, msg.answers),
            ("authority", header.nscount, msg.authorities),
            ("additional", header.arcount, msg.additionals),
        ):
            for _ in range(count):
                record, offset = _parse_record(data, offset, section)
                sink.append(record)
                parsed.append(record)
    except TruncatedError as exc:
        exc.records = parsed + exc.records  # 保留已解析记录
        raise
    return msg


# ---------------------------------------------------------------- 分组输出

def group_records(records: List[Record]) -> "Dict[Tuple[str, str], List[Record]]":
    """按 (名称, 类型) 分组；组内及组间均保持记录首次出现的原始顺序。"""
    groups: Dict[Tuple[str, str], List[Record]] = {}
    for rec in records:
        groups.setdefault((rec.name, rec.type), []).append(rec)
    return groups


def grouped_to_dict(records: List[Record]) -> Dict[str, List[dict]]:
    """group_records 的可 JSON 序列化版本，键为 "名称 类型"。"""
    out: Dict[str, List[dict]] = {}
    for (name, rtype), recs in group_records(records).items():
        out["%s %s" % (name, rtype)] = [r.to_dict() for r in recs]
    return out


# ---------------------------------------------------------------- TCP 流解析

@dataclass
class StreamResult:
    """parse_stream 的结果。"""
    messages: List[Message] = field(default_factory=list)
    error: Optional[TruncatedError] = None   # 流尾部截断/长度不符时非 None

    @property
    def records(self) -> List[Record]:
        return [r for m in self.messages for r in m.all_records()]

    def to_dict(self) -> dict:
        result = {
            "message_count": len(self.messages),
            "record_count": len(self.records),
            "messages": [m.to_dict() for m in self.messages],
            "grouped": grouped_to_dict(self.records),
        }
        if self.error is not None:
            result["error"] = {
                "type": "truncated",
                "missing_bytes": self.error.missing_bytes,
                "parsed_records": len(self.error.records) + len(self.records),
                "detail": str(self.error),
            }
        return result


def parse_stream(data: bytes) -> StreamResult:
    """解析 AXFR TCP 流：重复的 [2 字节大端长度][DNS 报文]。

    不会因截断抛异常；截断信息放在 StreamResult.error 中，
    已成功解析的报文与记录全部保留。
    """
    result = StreamResult()
    pos = 0
    while pos < len(data):
        if len(data) - pos < 2:  # 长度前缀本身不完整
            result.error = TruncatedError(2 - (len(data) - pos),
                                          detail="length prefix truncated")
            break
        (msg_len,) = struct.unpack_from("!H", data, pos)
        pos += 2
        if len(data) - pos < msg_len:  # 长度前缀与内容不符
            result.error = TruncatedError(
                msg_len - (len(data) - pos),
                detail="declared length %d exceeds available %d"
                       % (msg_len, len(data) - pos))
            break
        chunk = data[pos: pos + msg_len]
        try:
            result.messages.append(parse_message(chunk))
        except TruncatedError as exc:
            result.error = exc
            break
        pos += msg_len
    return result
