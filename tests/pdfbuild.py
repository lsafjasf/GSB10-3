# -*- coding: utf-8 -*-
"""测试夹具 PDF 生成器：传统偏移表 / XRef 流 / 对象流 / 增量更新。

仅用于自测与构造边界用例；实现与被测库互相独立，避免自证。
"""

import re
import zlib

from pdflite.objects import Name, Ref, PdfDict

HEADER = b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n"


class Stream:
    def __init__(self, d, content, compress=False):
        self.d = d if isinstance(d, PdfDict) else PdfDict(d)
        self.content = content
        self.compress = compress


def stream(d, content, compress=False):
    return Stream(d, content, compress)


def esc_literal(b: bytes) -> bytes:
    return b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def render_value(v) -> bytes:
    if isinstance(v, Name):
        return str(v).encode("latin-1")
    if isinstance(v, Ref):
        return ("%d %d R" % (v.num, v.gen)).encode()
    if isinstance(v, bool):
        return b"true" if v else b"false"
    if v is None:
        return b"null"
    if isinstance(v, int):
        return str(v).encode()
    if isinstance(v, float):
        return ("%g" % v).encode()
    if isinstance(v, bytes):
        return b"(" + esc_literal(v) + b")"
    if isinstance(v, list):
        return b"[ " + b" ".join(render_value(x) for x in v) + b" ]"
    if isinstance(v, PdfDict):
        parts = []
        for k, val in v.items():
            parts.append(str(k).encode("latin-1"))
            parts.append(render_value(val))
        return b"<< " + b" ".join(parts) + b" >>"
    raise TypeError("无法渲染类型 %r" % type(v))


def render_object(num, value, gen=0):
    head = ("%d %d obj\n" % (num, gen)).encode()
    if isinstance(value, Stream):
        s = value
        if s.compress:
            s.d[Name("/Filter")] = Name("/FlateDecode")
            body = zlib.compress(s.content)
        else:
            body = s.content
        s.d[Name("/Length")] = len(body)
        return head + render_value(s.d) + b"\nstream\n" + body + b"\nendstream\nendobj\n"
    return head + render_value(value) + b"\nendobj\n"


# ---------------------------------------------------------------- 页面树构造

def page_tree(page_count=3, leaf_kids=None, count_override=None):
    """单层页面树。返回 (objects, catalog_num, pages_num)。"""
    objs = {}
    pages_num = 2
    kids = []
    for i in range(page_count):
        pnum = 3 + i
        pd = PdfDict()
        pd[Name("/Type")] = Name("/Page")
        pd[Name("/Parent")] = Ref(pages_num, 0)
        if leaf_kids and leaf_kids[i]:
            for k, val in leaf_kids[i].items():
                pd[k] = val
        objs[pnum] = pd
        kids.append(Ref(pnum, 0))
    pages = PdfDict()
    pages[Name("/Type")] = Name("/Pages")
    pages[Name("/Kids")] = kids
    pages[Name("/Count")] = count_override if count_override is not None else page_count
    objs[pages_num] = pages
    catalog = PdfDict()
    catalog[Name("/Type")] = Name("/Catalog")
    catalog[Name("/Pages")] = Ref(pages_num, 0)
    objs[1] = catalog
    return objs, 1, pages_num


def nested_page_tree():
    """两级 /Pages：2(Count 4) -> [3(Count 2)->页4,5, 6(Count 2)->页7,8]。"""
    objs = {}

    def page(n, parent):
        d = PdfDict()
        d[Name("/Type")] = Name("/Page")
        d[Name("/Parent")] = Ref(parent, 0)
        objs[n] = d
        return Ref(n, 0)

    mid1 = PdfDict()
    mid1[Name("/Type")] = Name("/Pages")
    mid1[Name("/Kids")] = [page(4, 3), page(5, 3)]
    mid1[Name("/Count")] = 2
    mid1[Name("/Parent")] = Ref(2, 0)
    objs[3] = mid1

    mid2 = PdfDict()
    mid2[Name("/Type")] = Name("/Pages")
    mid2[Name("/Kids")] = [page(7, 6), page(8, 6)]
    mid2[Name("/Count")] = 2
    mid2[Name("/Parent")] = Ref(2, 0)
    objs[6] = mid2

    root = PdfDict()
    root[Name("/Type")] = Name("/Pages")
    root[Name("/Kids")] = [Ref(3, 0), Ref(6, 0)]
    root[Name("/Count")] = 4
    objs[2] = root

    catalog = PdfDict()
    catalog[Name("/Type")] = Name("/Catalog")
    catalog[Name("/Pages")] = Ref(2, 0)
    objs[1] = catalog
    return objs, 1, 2


# ---------------------------------------------------------------- 索引段

def _runs(nums):
    nums = sorted(nums)
    runs = []
    for n in nums:
        if runs and n == runs[-1][-1] + 1:
            runs[-1].append(n)
        else:
            runs.append([n])
    return runs


def _full_table(offsets, size):
    """完整传统 xref 表（含 0 号空闲条目），空洞写 free。"""
    seg = b"xref\n0 %d\n" % size
    for n in range(size):
        if n == 0:
            seg += b"0000000000 65535 f \n"
        elif n in offsets:
            seg += b"%010d 00000 n \n" % offsets[n]
        else:
            seg += b"0000000000 00000 f \n"
    return seg


def _partial_table(offsets):
    """增量更新用的稀疏 xref 表（只含本次对象，按连续段分组）。"""
    seg = b"xref\n"
    for g in _runs(sorted(offsets)):
        seg += b"%d %d\n" % (g[0], len(g))
        for n in g:
            seg += b"%010d 00000 n \n" % offsets[n]
    return seg


def _trailer(d):
    return b"trailer\n" + render_value(d) + b"\n"


def _xref_stream_bytes(entries, size, root_num, prev=None, index_arr=None,
                       extra_dict=None):
    """entries: {num: (type, f1, f2)}。返回 (xref对象值Stream, 需由调用者写文件)。"""
    w = [1, 4, 4]
    body = bytearray()
    nums = sorted(entries)
    for n in nums:
        t, a, b = entries[n]
        body += bytes([t]) + a.to_bytes(4, "big") + b.to_bytes(4, "big")
    d = PdfDict()
    d[Name("/Type")] = Name("/XRef")
    d[Name("/Size")] = size
    d[Name("/Root")] = Ref(root_num, 0)
    d[Name("/W")] = w
    if prev is not None:
        d[Name("/Prev")] = prev
    if index_arr is None:
        index_arr = []
        for g in _runs(nums):
            index_arr += [g[0], len(g)]
    d[Name("/Index")] = index_arr
    if extra_dict:
        for k, v in extra_dict.items():
            d[k] = v
    return Stream(d, bytes(body), compress=True)


# ---------------------------------------------------------------- 整文件

def build_pdf(objs, root_num, mode="table", compressed=None, size=None,
              extra_trailer=None, header=HEADER):
    """把 {num: 值} 组装成完整 PDF 字节。

    mode=table: 传统偏移表；mode=stream: XRef 流。
    compressed: {num: (对象流号, 流内序号)}，仅 stream 模式写类型 2 条目。
    """
    compressed = compressed or {}
    extra = extra_trailer if extra_trailer is not None else PdfDict()
    out = bytearray(header)
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += render_object(num, objs[num])

    max_obj = max(objs)
    if mode == "table":
        size = size or (max_obj + 1)
        xref_pos = len(out)
        out += _full_table(offsets, size)
        trailer = PdfDict()
        trailer[Name("/Size")] = size
        trailer[Name("/Root")] = Ref(root_num, 0)
        for k, v in extra.items():
            trailer[k] = v
        out += _trailer(trailer)
        out += b"startxref\n%d\n%%%%EOF\n" % xref_pos
        return bytes(out)

    if mode == "stream":
        xref_num = max_obj + 1
        size = size or (xref_num + 1)
        xref_pos = len(out)
        entries = {0: (0, 0, 65535)}
        for n, off in offsets.items():
            entries[n] = (1, off, 0)
        for n, (stm, idx) in compressed.items():
            entries[n] = (2, stm, idx)
        entries[xref_num] = (1, xref_pos, 0)
        xs = _xref_stream_bytes(entries, size, root_num, extra_dict=extra)
        out += render_object(xref_num, xs)
        out += b"startxref\n%d\n%%%%EOF\n" % xref_pos
        return bytes(out)

    raise ValueError("未知模式 %r" % mode)


def append_revision(data, new_objs, mode="table", root_num=1, prev=None,
                    compressed=None):
    """在 data 尾部追加一版增量更新，返回新的完整字节。"""
    compressed = compressed or {}
    if prev is None:
        m = list(re.finditer(rb"startxref\s*\n\s*(\d+)", data))
        if not m:
            raise ValueError("旧文件找不到 startxref")
        prev = int(m[-1].group(1))
    out = bytearray(data)
    if not out.endswith(b"\n"):
        out += b"\n"
    offsets = {}
    for num in sorted(new_objs):
        offsets[num] = len(out)
        out += render_object(num, new_objs[num])
    size = max(new_objs) + 1

    if mode == "table":
        xref_pos = len(out)
        out += _partial_table(offsets)
        trailer = PdfDict()
        trailer[Name("/Size")] = size
        trailer[Name("/Root")] = Ref(root_num, 0)
        trailer[Name("/Prev")] = prev
        out += _trailer(trailer)
        out += b"startxref\n%d\n%%%%EOF\n" % xref_pos
        return bytes(out)

    if mode == "stream":
        existing = [int(m.group(1)) for m in re.finditer(
            rb"(\d{1,10})[ \t]+\d{1,5}[ \t]+obj(?![0-9A-Za-z])", data)]
        xref_num = max([max(new_objs)] + existing) + 1
        size = xref_num + 1
        xref_pos = len(out)
        entries = {}
        for n, off in offsets.items():
            entries[n] = (1, off, 0)
        for n, (stm, idx) in compressed.items():
            entries[n] = (2, stm, idx)
        entries[xref_num] = (1, xref_pos, 0)
        xs = _xref_stream_bytes(entries, size, root_num, prev=prev)
        out += render_object(xref_num, xs)
        out += b"startxref\n%d\n%%%%EOF\n" % xref_pos
        return bytes(out)

    raise ValueError("未知模式 %r" % mode)


# ---------------------------------------------------------------- 对象流

def build_object_stream(num, members, compress=True):
    """members: [(对象号, 值), ...]，返回 (Stream, {对象号: (流号, 流内序号)})。"""
    header_parts = []
    body = bytearray()
    mapping = {}
    for idx, (obj_num, value) in enumerate(members):
        header_parts.append(("%d %d " % (obj_num, len(body))).encode())
        body += render_value(value)
        body += b" "
        mapping[obj_num] = (num, idx)
    header_bytes = b"".join(header_parts)
    content = header_bytes + bytes(body)
    d = PdfDict()
    d[Name("/Type")] = Name("/ObjStm")
    d[Name("/N")] = len(members)
    d[Name("/First")] = len(header_bytes)
    return Stream(d, content, compress=compress), mapping
