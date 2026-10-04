# -*- coding: utf-8 -*-
"""修复写回：以增量更新方式追加一条干净索引，不动原有对象字节。

- 无压缩对象：追加传统 xref 表段；
- 含压缩对象：追加 XRef 流段（复用文件中现存的对象流，类型 2 条目照旧指向它）。
"""

import zlib

from .objects import Name, Ref, PdfDict


def _runs(nums):
    nums = sorted(nums)
    runs = []
    for n in nums:
        if runs and n == runs[-1][-1] + 1:
            runs[-1].append(n)
        else:
            runs.append([n])
    return runs


def repaired_bytes(doc) -> bytes:
    """基于扫描结果生成“原文件 + 修复索引段”的新字节。"""
    out = bytearray(doc.data)
    if out and not out.endswith(b"\n"):
        out += b"\n"
    root_num = doc.catalog_num()
    if root_num is None:
        raise ValueError("找不到 /Catalog，无法写回 /Root")
    size = max(max(doc.scan_map), root_num) + 2

    if not doc.compressed:
        return _append_table(out, doc, size, root_num)
    return _append_xref_stream(out, doc, size, root_num)


def _append_table(out, doc, size, root_num):
    offsets = {num: rec.offset for num, rec in doc.scan_map.items()}
    seg = b"xref\n"
    for g in _runs(sorted(offsets)):
        seg += b"%d %d\n" % (g[0], len(g))
        for n in g:
            seg += b"%010d 00000 n \n" % offsets[n]
    xref_pos = len(out)
    out += seg
    d = PdfDict()
    d[Name("/Size")] = size
    d[Name("/Root")] = Ref(root_num, 0)
    out += b"trailer\n" + _render_dict(d) + b"\n"
    out += b"startxref\n%d\n%%%%EOF\n" % xref_pos
    return bytes(out)


def _append_xref_stream(out, doc, size, root_num):
    xref_num = max(doc.scan_map) + 1
    xref_pos = len(out)
    entries = {0: (0, 0, 65535)}
    for num, rec in doc.scan_map.items():
        entries[num] = (1, rec.offset, 0)
    for num, (stm, idx) in doc.compressed.items():
        entries[num] = (2, stm, idx)
    entries[xref_num] = (1, xref_pos, 0)

    body = bytearray()
    nums = sorted(entries)
    for n in nums:
        t, a, b = entries[n]
        body += bytes([t]) + a.to_bytes(4, "big") + b.to_bytes(4, "big")
    index_arr = []
    for g in _runs(nums):
        index_arr += [g[0], len(g)]

    d = PdfDict()
    d[Name("/Type")] = Name("/XRef")
    d[Name("/Size")] = size
    d[Name("/Root")] = Ref(root_num, 0)
    d[Name("/W")] = [1, 4, 4]
    d[Name("/Index")] = index_arr
    raw = zlib.compress(bytes(body))
    d[Name("/Length")] = len(raw)
    d[Name("/Filter")] = Name("/FlateDecode")
    out += ("%d 0 obj\n" % xref_num).encode()
    out += _render_dict(d)
    out += b"\nstream\n" + raw + b"\nendstream\nendobj\n"
    out += b"startxref\n%d\n%%%%EOF\n" % xref_pos
    return bytes(out)


def _render_dict(d):
    parts = [b"<<"]
    for k, v in d.items():
        parts.append(str(k).encode("latin-1"))
        if isinstance(v, Name):
            parts.append(str(v).encode())
        elif isinstance(v, Ref):
            parts.append(("%d %d R" % (v.num, v.gen)).encode())
        elif isinstance(v, bool):
            parts.append(b"true" if v else b"false")
        elif isinstance(v, list):
            inner = b" ".join(("%d" % x).encode() for x in v)
            parts.append(b"[" + inner + b"]")
        else:
            parts.append(str(v).encode())
    parts.append(b">>")
    return b" ".join(parts)
