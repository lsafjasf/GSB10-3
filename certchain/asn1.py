"""极简 DER/ASN.1 解码器，只覆盖 X.509 证书解析所需的部分。

不处理 BER/不确定长度（DER 不允许），输入非 DER 时抛 ASN1Error。
"""

from __future__ import annotations


class ASN1Error(ValueError):
    """DER 数据格式不合法。"""


# tag 类别
CLASS_UNIVERSAL = 0
CLASS_APPLICATION = 1
CLASS_CONTEXT = 2
CLASS_PRIVATE = 3

# 通用 tag
TAG_BOOLEAN = 0x01
TAG_INTEGER = 0x02
TAG_BIT_STRING = 0x03
TAG_OCTET_STRING = 0x04
TAG_NULL = 0x05
TAG_OID = 0x06
TAG_UTF8_STRING = 0x0C
TAG_PRINTABLE_STRING = 0x13
TAG_T61_STRING = 0x14
TAG_IA5_STRING = 0x16
TAG_UTC_TIME = 0x17
TAG_GENERALIZED_TIME = 0x18
TAG_UNIVERSAL_STRING = 0x1C
TAG_BMP_STRING = 0x1E


class Node:
    __slots__ = ("tag_class", "constructed", "tag", "content", "children", "raw")

    def __init__(self, tag_class, constructed, tag, content, raw, children=None):
        self.tag_class = tag_class
        self.constructed = constructed
        self.tag = tag
        self.content = content
        self.raw = raw
        self.children = children

    def __repr__(self):
        return (
            f"Node(class={self.tag_class}, tag={self.tag}, "
            f"constructed={self.constructed}, len={len(self.content)})"
        )


def _parse_one(data: bytes, offset: int):
    if offset >= len(data):
        raise ASN1Error("意外的 DER 结尾")
    first = data[offset]
    tag_class = first >> 6
    constructed = bool(first & 0x20)
    tag = first & 0x1F
    pos = offset + 1
    if tag == 0x1F:  # 高 tag 号（本项目用不到，仅做正确解析）
        tag = 0
        while True:
            if pos >= len(data):
                raise ASN1Error("tag 编码被截断")
            b = data[pos]
            pos += 1
            tag = (tag << 7) | (b & 0x7F)
            if not b & 0x80:
                break
    if pos >= len(data):
        raise ASN1Error("长度字节被截断")
    lb = data[pos]
    pos += 1
    if lb & 0x80:
        n = lb & 0x7F
        if n == 0:
            raise ASN1Error("DER 不允许不确定长度")
        if n > 8:
            raise ASN1Error("长度字段过大")
        length = int.from_bytes(data[pos:pos + n], "big")
        pos += n
    else:
        length = lb
    end = pos + length
    if end > len(data):
        raise ASN1Error("内容长度超出数据边界")
    content = data[pos:end]
    raw = data[offset:end]
    node = Node(tag_class, constructed, tag, content, raw)
    if constructed:
        children = []
        inner = 0
        while inner < len(content):
            child, inner = _parse_one(content, inner)
            children.append(child)
        node.children = children
    return node, end


def parse(data: bytes, offset: int = 0):
    """解析一个 TLV，返回 (Node, 下一个偏移)。"""
    return _parse_one(data, offset)


def parse_all(data: bytes):
    nodes = []
    pos = 0
    while pos < len(data):
        node, pos = _parse_one(data, pos)
        nodes.append(node)
    return nodes


def expect(node: Node, tag: int, tag_class: int = CLASS_UNIVERSAL):
    if node.tag_class != tag_class or node.tag != tag:
        raise ASN1Error(f"期望 tag({tag_class},{tag})，实际为 ({node.tag_class},{node.tag})")
    return node


def seq_of(node: Node):
    expect(node, 0x10)
    return node.children or []


def context(node: Node, tag: int):
    """取显式 context 标记内的子节点。"""
    expect(node, tag, CLASS_CONTEXT)
    children = node.children
    if not children:
        raise ASN1Error(f"context [{tag}] 为空")
    return children[0]


def decode_oid(data: bytes) -> str:
    if not data:
        raise ASN1Error("空 OID")
    arcs = [str(data[0] // 40), str(data[0] % 40)]
    value = 0
    for b in data[1:]:
        value = (value << 7) | (b & 0x7F)
        if not b & 0x80:
            arcs.append(str(value))
            value = 0
    return ".".join(arcs)


def decode_string(node: Node) -> str:
    c = node.content
    if node.tag == TAG_UTF8_STRING:
        return c.decode("utf-8")
    if node.tag in (TAG_PRINTABLE_STRING, TAG_IA5_STRING, TAG_T61_STRING):
        return c.decode("latin-1")
    if node.tag == TAG_BMP_STRING:
        return c.decode("utf-16-be")
    if node.tag == TAG_UNIVERSAL_STRING:
        return c.decode("utf-32-be")
    raise ASN1Error(f"不支持的字符串类型 tag={node.tag}")


def decode_bool(node: Node) -> bool:
    expect(node, TAG_BOOLEAN)
    return node.content != b"\x00"


def decode_int(node: Node) -> int:
    expect(node, TAG_INTEGER)
    return int.from_bytes(node.content, "big", signed=True)


def decode_bitstring(node: Node) -> bytes:
    expect(node, TAG_BIT_STRING)
    if not node.content:
        return b""
    return node.content[1:]
