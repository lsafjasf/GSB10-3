"""Minimal DER (ASN.1) parser -- standard library only.

Only what is needed to parse X.509 certificates: TLV decoding, a handful of
universal types, and context-specific tags (explicit and implicit).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple


class DerError(ValueError):
    """Raised when input bytes are not well-formed DER."""


@dataclass
class Node:
    tag_class: int          # 0=universal 1=application 2=context 3=private
    constructed: bool
    tag: int
    content: bytes          # raw content octets (children concatenated if constructed)
    children: List["Node"] = field(default_factory=list)
    raw: bytes = b""        # full TLV encoding of this node

    def child(self, index: int) -> "Node":
        return self.children[index]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        cls = ("UNIVERSAL", "APPLICATION", "CONTEXT", "PRIVATE")[self.tag_class]
        cons = "CONS" if self.constructed else "PRIM"
        return f"<DER {cls} {cons} tag={self.tag} len={len(self.content)}>"


def _read_length(data: bytes, off: int) -> Tuple[int, int]:
    if off >= len(data):
        raise DerError("truncated length")
    first = data[off]
    off += 1
    if first < 0x80:
        return first, off
    num = first & 0x7F
    if num == 0:
        raise DerError("indefinite length not allowed in DER")
    if num > 4 or off + num > len(data):
        raise DerError("bad long-form length")
    length = int.from_bytes(data[off:off + num], "big")
    if length < 0x80:
        raise DerError("non-minimal length encoding")
    return length, off + num


def parse_one(data: bytes, off: int = 0) -> Tuple[Node, int]:
    """Parse a single TLV starting at *off*; return (node, next_offset)."""
    start = off
    if off >= len(data):
        raise DerError("truncated tag")
    first = data[off]
    off += 1
    tag_class = first >> 6
    constructed = bool(first & 0x20)
    tag = first & 0x1F
    if tag == 0x1F:
        tag = 0
        while True:
            if off >= len(data):
                raise DerError("truncated high-tag-number")
            b = data[off]
            off += 1
            tag = (tag << 7) | (b & 0x7F)
            if not (b & 0x80):
                break
    length, off = _read_length(data, off)
    end = off + length
    if end > len(data):
        raise DerError("content overruns buffer")
    content = data[off:end]
    node = Node(tag_class, constructed, tag, content, raw=data[start:end])
    if constructed:
        coff = 0
        while coff < len(content):
            child, coff = parse_one(content, coff)
            node.children.append(child)
    return node, end


def parse(data: bytes) -> Node:
    node, off = parse_one(data, 0)
    if off != len(data):
        raise DerError("trailing bytes after top-level TLV")
    return node


def expect(node: Node, tag: int, tag_class: int = 0) -> Node:
    if node.tag_class != tag_class or node.tag != tag:
        raise DerError(f"unexpected tag: class={node.tag_class} tag={node.tag}, "
                       f"expected class={tag_class} tag={tag}")
    return node


def to_int(node: Node) -> int:
    expect(node, 2)
    if not node.content:
        raise DerError("empty INTEGER")
    return int.from_bytes(node.content, "big", signed=True)


def to_uint(node: Node) -> int:
    expect(node, 2)
    if not node.content:
        raise DerError("empty INTEGER")
    if node.content[0] & 0x80:
        raise DerError("negative INTEGER where non-negative expected")
    return int.from_bytes(node.content, "big")


def to_oid(node: Node) -> str:
    expect(node, 6)
    body = node.content
    if not body:
        raise DerError("empty OID")
    first = body[0]
    parts = [str(min(first // 40, 2)), str(first - 40 * min(first // 40, 2))]
    value = 0
    for b in body[1:]:
        value = (value << 7) | (b & 0x7F)
        if not (b & 0x80):
            parts.append(str(value))
            value = 0
    if value:
        raise DerError("truncated OID arc")
    return ".".join(parts)


def to_bit_string(node: Node) -> bytes:
    """Return the payload bytes of a BIT STRING (requires byte-aligned content)."""
    expect(node, 3)
    if not node.content:
        raise DerError("empty BIT STRING")
    unused = node.content[0]
    if unused != 0:
        raise DerError("bit string with unused bits not supported here")
    return node.content[1:]


def to_time(node: Node) -> datetime:
    if node.tag == 23:  # UTCTime  YYMMDDHHMMSSZ
        text = node.content.decode("ascii")
        if len(text) != 13 or not text.endswith("Z"):
            raise DerError(f"unsupported UTCTime: {text!r}")
        year = int(text[0:2])
        year += 1900 if year >= 50 else 2000
        return datetime(year, int(text[2:4]), int(text[4:6]),
                        int(text[6:8]), int(text[8:10]), int(text[10:12]),
                        tzinfo=timezone.utc)
    if node.tag == 24:  # GeneralizedTime  YYYYMMDDHHMMSSZ
        text = node.content.decode("ascii")
        if len(text) != 15 or not text.endswith("Z"):
            raise DerError(f"unsupported GeneralizedTime: {text!r}")
        return datetime(int(text[0:4]), int(text[4:6]), int(text[6:8]),
                        int(text[8:10]), int(text[10:12]), int(text[12:14]),
                        tzinfo=timezone.utc)
    raise DerError(f"not a time node: tag={node.tag}")


def to_text(node: Node) -> str:
    """Decode a DirectoryString-ish primitive to str (best effort)."""
    if node.tag == 12:   # UTF8String
        return node.content.decode("utf-8")
    if node.tag == 19:   # PrintableString
        return node.content.decode("ascii")
    if node.tag == 22:   # IA5String
        return node.content.decode("ascii")
    if node.tag == 20:   # TeletexString (treated as latin-1, common practice)
        return node.content.decode("latin-1")
    if node.tag == 30:   # BMPString
        return node.content.decode("utf-16-be")
    return node.content.decode("utf-8", errors="replace")
