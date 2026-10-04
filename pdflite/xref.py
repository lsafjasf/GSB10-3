# -*- coding: utf-8 -*-
"""交叉引用索引：传统偏移表(xref 表)与流式索引(XRef stream)统一解析。

- 从 startxref 出发沿 /Prev 链向前收集所有索引段（最新在前）。
- 表与流可混合（增量更新常见）。
- 解析损坏时抛 PdfParseError，由上层决定是否回退到顺序扫描重建。
"""

import re

from .objects import Name, PdfDict, PdfStream, XRefEntry, XRefSection
from .parser import Parser, PdfParseError
from .filters import decode_stream, FilterError

_ENTRY_RE = re.compile(rb"^(\d{1,10})[ \t]+(\d{1,5})[ \t]+([nf])[ \t]*\r?$")
_SUBSEC_RE = re.compile(rb"^(\d+)[ \t]+(\d+)[ \t]*\r?$")


def find_startxref(data: bytes):
    """从文件尾部查找 startxref 偏移；找不到返回 None。"""
    tail = data[-2048:] if len(data) > 2048 else data
    idx = tail.rfind(b"startxref")
    if idx < 0:
        return None
    pos = idx + len(b"startxref")
    m = re.match(rb"\s*(\d+)", tail[pos:])
    if not m:
        return None
    return int(m.group(1))


def load_xref_chain(data: bytes, start: int):
    """返回 (sections, trailer)。sections 按从新到旧排列。"""
    parser = Parser(data)
    sections = []
    trailer = PdfDict()
    seen = set()
    offset = start
    while offset is not None:
        if offset in seen:
            raise PdfParseError("xref /Prev 链出现循环", offset)
        seen.add(offset)
        if offset < 0 or offset >= len(data):
            raise PdfParseError("xref 偏移越界: %d" % offset, offset)
        parser.pos = offset
        parser._skip_ws()
        offset = parser.pos
        if data[offset:offset + 4] == b"xref":
            section = _parse_table(data, offset)
        else:
            section = _parse_stream_section(data, offset, parser)
        sections.append(section)
        if section.parse_error:
            raise PdfParseError(section.parse_error, offset)
        for k, v in section.trailer.items():
            trailer.setdefault(k, v)
        prev = section.trailer.get(Name("/Prev"))
        offset = prev if isinstance(prev, int) else None
    return sections, trailer


# ---------------- 传统偏移表 ----------------

def _read_line(data: bytes, pos: int):
    """读一行（兼容 LF / CRLF / 裸 CR），返回 (line_bytes_without_eol, next_pos)。"""
    n = len(data)
    i = pos
    while i < n and data[i] not in b"\r\n":
        i += 1
    line = data[pos:i]
    if i < n:
        if data[i:i + 2] == b"\r\n":
            i += 2
        else:
            i += 1
    return line, i


def _parse_table(data: bytes, offset: int) -> XRefSection:
    section = XRefSection(offset=offset, is_stream=False)
    pos = offset + 4  # 跳过 'xref'
    entries = {}
    while True:
        line_start = pos
        line, pos = _read_line(data, pos)
        line = line.strip()
        if not line:
            continue
        if line.startswith(b"trailer"):
            # 兼容 "trailer <<...>>" 写在同一行的写法：定位到关键字之后
            pos = data.index(b"trailer", line_start) + len(b"trailer")
            break
        m = _SUBSEC_RE.match(line)
        if not m:
            raise PdfParseError("xref 子段头非法: %r" % line[:40], offset)
        first, count = int(m.group(1)), int(m.group(2))
        for i in range(count):
            eline, pos = _read_line(data, pos)
            em = _ENTRY_RE.match(eline)
            if not em:
                raise PdfParseError("xref 条目非法: %r" % eline[:40], offset)
            num = first + i
            field2 = int(em.group(1))
            gen = int(em.group(2))
            inuse = em.group(3) == b"n"
            entries[num] = XRefEntry(
                num=num,
                kind=0 if inuse else 1,
                field2=field2,
                gen=gen,
                section_offset=offset,
            )
    # trailer 字典
    parser = Parser(data, pos)
    trailer = parser.parse_value()
    if not isinstance(trailer, PdfDict):
        raise PdfParseError("trailer 不是字典", pos)
    section.entries = entries
    section.trailer = trailer
    return section


# ---------------- 流式索引（XRef stream） ----------------

def _parse_stream_section(data: bytes, offset: int, parser: Parser) -> XRefSection:
    obj = parser.parse_indirect(offset)
    if not isinstance(obj.value, PdfStream):
        raise PdfParseError("startxref 指向的不是 xref 表或 XRef 流", offset)
    d = obj.value.dict
    if d.get_name("/Type") != "/XRef":
        raise PdfParseError("流对象 /Type 不是 /XRef", offset)
    try:
        raw = decode_stream(obj.value)
    except FilterError as exc:
        raise PdfParseError("XRef 流解码失败: %s" % exc, offset)

    w = d.get(Name("/W"))
    if not (isinstance(w, list) and len(w) == 3 and all(isinstance(x, int) for x in w)):
        raise PdfParseError("XRef 流缺少合法的 /W", offset)
    size = d.get(Name("/Size"))
    if not isinstance(size, int):
        raise PdfParseError("XRef 流缺少 /Size", offset)
    index = d.get(Name("/Index"))
    if index is None:
        index = [0, size]
    if not (isinstance(index, list) and len(index) % 2 == 0):
        raise PdfParseError("XRef 流 /Index 非法", offset)

    w0, w1, w2 = w
    entry_len = w0 + w1 + w2
    if entry_len <= 0:
        raise PdfParseError("XRef 流 /W 全为 0", offset)

    entries = {}
    p = 0
    for i in range(0, len(index), 2):
        first, count = index[i], index[i + 1]
        for j in range(count):
            if p + entry_len > len(raw):
                raise PdfParseError("XRef 流数据长度不足", offset)
            f0 = int.from_bytes(raw[p:p + w0], "big") if w0 else 1  # 缺省类型为 1
            f1 = int.from_bytes(raw[p + w0:p + w0 + w1], "big") if w1 else 0
            f2 = int.from_bytes(raw[p + w0 + w1:p + entry_len], "big") if w2 else 0
            p += entry_len
            num = first + j
            if f0 == 0:
                entries[num] = XRefEntry(num, 1, f1, f2, offset)
            elif f0 == 1:
                entries[num] = XRefEntry(num, 0, f1, f2, offset)
            elif f0 == 2:
                entries[num] = XRefEntry(num, 2, f1, f2, offset)
            # 其它类型按规范保留给未来，忽略

    section = XRefSection(offset=offset, is_stream=True)
    section.entries = entries
    section.trailer = d  # XRef 流字典本身即 trailer
    return section
