"""设备日志报文解析库（仅使用 Python 标准库）。

报文格式（长度框架 + 传统优先级/主机头 + 可选结构化键值块）::

    frame    := [length SP] body
    length   := body 的十进制字节数（禁止前导零）
    body     := "<" pri ">" host SP [sd] message
    pri      := 0..191；facility = pri // 8，severity = pri % 8
    host     := 1..255 个可见 ASCII 字符（不含空格）
    sd       := "[" [pair *(SP pair)] "]"
    pair     := key "=" qvalue
    key      := 1*[A-Za-z0-9_.-]
    qvalue   := '"' *(普通字符 / 转义序列) '"'
    转义序列 := "\" ("\"" / "\" / "]" / "n" / "t")
    message  := 剩余全部字节（可为空，允许非 ASCII / UTF-8）

qvalue 中 ``"``、``\\``、``]`` 以及控制字符必须转义，因此合法报文
的规范化字节形式唯一：parse 后再 rebuild 必然逐字节一致。
"""

from __future__ import annotations

import dataclasses
import re
from typing import Optional

__all__ = [
    "FACILITY_NAMES",
    "SEVERITY_NAMES",
    "MAX_PRI",
    "MAX_HOST_LEN",
    "LogParseError",
    "LogMessage",
    "StructuredEntry",
    "parse",
]

MAX_PRI = 191
MAX_HOST_LEN = 255

FACILITY_NAMES = (
    "kern", "user", "mail", "daemon", "auth", "syslog", "lpr", "news",
    "uucp", "cron", "authpriv", "ftp", "ntp", "audit", "alert", "cron2",
    "local0", "local1", "local2", "local3", "local4", "local5",
    "local6", "local7",
)

SEVERITY_NAMES = (
    "emergency", "alert", "critical", "error",
    "warning", "notice", "informational", "debug",
)

_KEY_RE = re.compile(rb"[A-Za-z0-9_.\-]+")
_ESCAPES = {0x22: '"', 0x5C: "\\", 0x5D: "]", 0x6E: "\n", 0x74: "\t"}


class LogParseError(ValueError):
    """报文不合法时抛出（含长度声明错误、优先级越界等）。"""


@dataclasses.dataclass(frozen=True)
class StructuredEntry:
    """结构化块中的一个键值对。

    order: 在整块中的全局出现序号（从 0 开始）
    occurrence: 同一 key 的第几次出现（从 0 开始）
    """

    key: str
    value: str
    order: int
    occurrence: int

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "order": self.order,
            "occurrence": self.occurrence,
            "value": self.value,
        }


def _is_canonical_number(token: bytes) -> bool:
    return bool(token) and token.isdigit() and not (
        len(token) > 1 and token[0] == 0x30
    )


def _escape_value(value: str) -> bytes:
    out = bytearray()
    for ch in value:
        if ch in ('"', "\\", "]"):
            out += b"\\" + ch.encode("ascii")
        elif ch == "\n":
            out += b"\\n"
        elif ch == "\t":
            out += b"\\t"
        elif ord(ch) < 0x20:
            raise ValueError(f"取值含不可表示的控制字符 U+{ord(ch):04X}")
        else:
            out += ch.encode("utf-8", "surrogateescape")
    return bytes(out)


def _parse_qvalue(body: bytes, pos: int, key: str) -> tuple[str, int]:
    """解析带引号的取值，pos 指向开头引号的下一位置。"""

    out = bytearray()
    total = len(body)
    while pos < total:
        byte = body[pos]
        if byte == 0x22:  # '"'
            return out.decode("utf-8", "surrogateescape"), pos + 1
        if byte == 0x5C:  # '\\'
            if pos + 1 >= total:
                raise LogParseError(f"键 {key!r} 的取值在转义符处被截断")
            escaped = body[pos + 1]
            if escaped not in _ESCAPES:
                raise LogParseError(
                    f"键 {key!r} 的取值含非法转义序列: \\{chr(escaped)}"
                )
            out += _ESCAPES[escaped].encode("utf-8")
            pos += 2
            continue
        if byte == 0x5D:  # ']' 必须转义
            raise LogParseError(f"键 {key!r} 的取值含未转义的 ']'")
        if byte < 0x20:
            raise LogParseError(f"键 {key!r} 的取值含未转义的控制字符")
        out.append(byte)
        pos += 1
    raise LogParseError(f"键 {key!r} 的取值缺少结束引号")


def _parse_sd(body: bytes, pos: int) -> tuple[list[StructuredEntry], int]:
    """解析结构化块，pos 指向 '['。"""

    entries: list[StructuredEntry] = []
    seen: dict[str, int] = {}
    total = len(body)
    pos += 1  # 跳过 '['
    if pos < total and body[pos:pos + 1] == b"]":
        return entries, pos + 1

    while True:
        match = _KEY_RE.match(body, pos)
        if not match or match.start() != pos:
            raise LogParseError(f"结构化块键名非法（偏移 {pos}）")
        key = match.group().decode("ascii")
        pos = match.end()
        if pos >= total or body[pos:pos + 1] != b"=":
            raise LogParseError(f"键 {key!r} 后缺少 '='")
        pos += 1
        if pos >= total or body[pos:pos + 1] != b'"':
            raise LogParseError(f"键 {key!r} 的取值缺少开始引号")
        pos += 1
        value, pos = _parse_qvalue(body, pos, key)

        occurrence = seen.get(key, 0)
        seen[key] = occurrence + 1
        entries.append(StructuredEntry(
            key=key, value=value,
            order=len(entries), occurrence=occurrence,
        ))

        if pos >= total:
            raise LogParseError("结构化块缺少 ']' 结束符")
        delim = body[pos:pos + 1]
        if delim == b"]":
            return entries, pos + 1
        if delim != b" ":
            raise LogParseError(f"键值对之间缺少空格分隔（偏移 {pos}）")
        pos += 1


class LogMessage:
    """解析结果。

    structured 为 None 表示报文没有结构化块；为 [] 表示显式的空块 "[]"。
    """

    __slots__ = ("framed", "pri", "host", "structured", "message")

    def __init__(self, *, framed: bool, pri: int, host: str,
                 structured: Optional[list[StructuredEntry]], message: str):
        self.framed = framed
        self.pri = pri
        self.host = host
        self.structured = structured
        self.message = message

    @property
    def facility(self) -> int:
        return self.pri // 8

    @property
    def severity(self) -> int:
        return self.pri % 8

    @property
    def facility_name(self) -> str:
        return FACILITY_NAMES[self.facility]

    @property
    def severity_name(self) -> str:
        return SEVERITY_NAMES[self.severity]

    @property
    def structured_map(self) -> Optional[dict[str, list[str]]]:
        """键 -> 按出现顺序排列的全部取值；重复键不会丢失。"""

        if self.structured is None:
            return None
        grouped: dict[str, list[str]] = {}
        for entry in self.structured:
            grouped.setdefault(entry.key, []).append(entry.value)
        return grouped

    def rebuild(self) -> bytes:
        """按规范化形式重建报文；合法输入下与原报文逐字节一致。"""

        body = bytearray()
        body += b"<" + str(self.pri).encode("ascii") + b">"
        body += self.host.encode("ascii")
        body += b" "
        if self.structured is not None:
            pairs = b" ".join(
                entry.key.encode("ascii")
                + b'="' + _escape_value(entry.value) + b'"'
                for entry in self.structured
            )
            body += b"[" + pairs + b"]"
            if self.message:
                body += b" "
        body += self.message.encode("utf-8", "surrogateescape")
        if self.framed:
            return str(len(body)).encode("ascii") + b" " + bytes(body)
        return bytes(body)

    def to_dict(self) -> dict:
        return {
            "framed": self.framed,
            "pri": self.pri,
            "facility": self.facility,
            "facility_name": self.facility_name,
            "severity": self.severity,
            "severity_name": self.severity_name,
            "host": self.host,
            "structured": (
                None if self.structured is None
                else [entry.to_dict() for entry in self.structured]
            ),
            "structured_map": self.structured_map,
            "message": self.message,
        }


def parse(data: bytes) -> LogMessage:
    """解析一整条日志报文（bytes），返回 :class:`LogMessage`。"""

    if isinstance(data, str):
        raise TypeError("parse 需要 bytes 输入")
    if not data:
        raise LogParseError("空报文")

    framed = False
    body = data
    if 0x30 <= data[0] <= 0x39:  # 以数字开头：带长度声明的帧
        framed = True
        sep = data.find(b" ")
        if sep < 0:
            raise LogParseError("长度声明后缺少空格分隔符")
        length_token = data[:sep]
        if not _is_canonical_number(length_token):
            raise LogParseError(f"长度声明格式非法: {length_token!r}")
        declared = int(length_token)
        body = data[sep + 1:]
        if declared != len(body):
            raise LogParseError(
                f"长度声明错误: 声明 {declared} 字节，实际 {len(body)} 字节"
            )

    total = len(body)
    pos = 0
    if body[pos:pos + 1] != b"<":
        raise LogParseError("缺少 '<' 起始的优先级字段")
    pri_end = body.find(b">", pos + 1)
    if pri_end < 0:
        raise LogParseError("优先级字段缺少 '>' 结束符")
    pri_token = body[pos + 1:pri_end]
    if not _is_canonical_number(pri_token):
        raise LogParseError(f"优先级字段非法: {pri_token!r}")
    pri = int(pri_token)
    if pri > MAX_PRI:
        # 越界必须报错，禁止用取模/截断的方式“兼容”
        raise LogParseError(
            f"优先级越界: {pri}（合法范围 0-{MAX_PRI}）"
        )
    pos = pri_end + 1

    host_end = body.find(b" ", pos)
    if host_end < 0:
        raise LogParseError("主机字段后缺少空格分隔符")
    host_bytes = body[pos:host_end]
    if not host_bytes or len(host_bytes) > MAX_HOST_LEN:
        raise LogParseError("主机字段长度非法（1-255 字节）")
    if any(byte < 0x21 or byte > 0x7E for byte in host_bytes):
        raise LogParseError("主机字段含非法字符（要求可见 ASCII）")
    host = host_bytes.decode("ascii")
    pos = host_end + 1

    structured: Optional[list[StructuredEntry]] = None
    if pos < total and body[pos:pos + 1] == b"[":
        structured, pos = _parse_sd(body, pos)
        if pos < total:
            if body[pos:pos + 1] != b" ":
                raise LogParseError("结构化块后缺少空格分隔符")
            pos += 1

    message = body[pos:].decode("utf-8", "surrogateescape")
    return LogMessage(
        framed=framed, pri=pri, host=host,
        structured=structured, message=message,
    )
