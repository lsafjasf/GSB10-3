# -*- coding: utf-8 -*-
"""边界用例夹具工厂：返回 (文件名, 字节, 说明) 列表。"""

import re

from pdflite.objects import Name, Ref, PdfDict
from tests import pdfbuild as B

FIXTURES = []


def fixture(name, desc):
    def deco(fn):
        FIXTURES.append((name, desc, fn))
        return fn
    return deco


def _leaf(t):
    return PdfDict({
        Name("/Type"): Name("/Page"),
        Name("/Parent"): Ref(2, 0),
        Name("/Label"): t,
    })


@fixture("01_classic_table.pdf", "传统偏移表，3 页，内容流含伪对象头")
def f_classic():
    objs, root, _ = B.page_tree(3)
    # 故意写入一个会被词法误当对象头的字符串，验证扫描器能过滤
    objs[6] = B.stream(PdfDict(),
                       b"BT /F1 12 Tf 72 700 Td (123 0 obj << /Fake true >> not endobj) Tj ET")
    objs[3][Name("/Contents")] = Ref(6, 0)
    return B.build_pdf(objs, root, mode="table")


@fixture("02_xref_stream_nested.pdf", "流式索引，两级页面树共 4 页")
def f_nested_stream():
    objs, root, _ = B.nested_page_tree()
    return B.build_pdf(objs, root, mode="stream")


@fixture("03_object_stream.pdf", "对象流：3 页全部压缩进 ObjStm 6")
def f_objstm():
    objs = {}
    objs[1] = PdfDict({Name("/Type"): Name("/Catalog"), Name("/Pages"): Ref(2, 0)})
    objs[2] = PdfDict({Name("/Type"): Name("/Pages"), Name("/Count"): 3,
                       Name("/Kids"): [Ref(3, 0), Ref(4, 0), Ref(5, 0)]})
    stm, mapping = B.build_object_stream(
        6, [(3, _leaf(b"A")), (4, _leaf(b"B")), (5, _leaf(b"C"))])
    objs[6] = stm
    return B.build_pdf(objs, 1, mode="stream", compressed=mapping)


@fixture("04_incremental_classic.pdf", "增量更新(表->表)：对象 2 被覆盖，新增第 3 页")
def f_inc_classic():
    objs, root, _ = B.page_tree(2)
    base = B.build_pdf(objs, root, mode="table")
    new_pages = PdfDict({Name("/Type"): Name("/Pages"), Name("/Count"): 3,
                         Name("/Kids"): [Ref(3, 0), Ref(4, 0), Ref(5, 0)]})
    objs5 = PdfDict({Name("/Type"): Name("/Page"), Name("/Parent"): Ref(2, 0)})
    return B.append_revision(base, {2: new_pages, 5: objs5}, mode="table")


@fixture("05_incremental_mixed.pdf", "增量更新(表->流)混合索引：对象 2 被覆盖")
def f_inc_mixed():
    objs, root, _ = B.page_tree(2)
    base = B.build_pdf(objs, root, mode="table")
    new_pages = PdfDict({Name("/Type"): Name("/Pages"), Name("/Count"): 2,
                         Name("/Kids"): [Ref(3, 0), Ref(4, 0)]})
    return B.append_revision(base, {2: new_pages}, mode="stream")


@fixture("06_offsets_invalid.pdf", "传统表内全部在用条目偏移被改成越界值")
def f_bad_offsets():
    objs, root, _ = B.page_tree(3)
    data = bytearray(B.build_pdf(objs, root, mode="table"))
    i = data.index(b"xref\n")
    j = data.index(b"trailer", i)
    region = bytes(data[i:j])
    region = re.sub(rb"\d{10}(?= 00000 n )", b"9999999999", region)
    data[i:j] = region
    return bytes(data)


@fixture("07_startxref_invalid.pdf", "startxref 数值被清零，索引入口失效")
def f_bad_startxref():
    objs, root, _ = B.page_tree(3)
    data = bytearray(B.build_pdf(objs, root, mode="table"))
    pos = data.rfind(b"startxref")
    m = re.compile(rb"\d+").search(bytes(data), pos)
    data[m.start():m.end()] = b"0" * (m.end() - m.start())
    return bytes(data)


@fixture("08_truncated_classic.pdf", "尾部被截断：xref 表及之后全部丢失")
def f_trunc_classic():
    objs, root, _ = B.page_tree(3)
    data = B.build_pdf(objs, root, mode="table")
    return data[:data.index(b"xref\n")]


@fixture("09_truncated_stream_recover.pdf",
         "流式索引文件截断：仅剩 XRef 流、startxref 丢失，可从扫描恢复映射")
def f_trunc_stream():
    data = f_objstm()
    pos = data.rfind(b"startxref")
    return data[:pos]


@fixture("10_count_mismatch.pdf", "页面树 /Count 声明 99，实际只有 3 页")
def f_count_mismatch():
    objs, root, _ = B.page_tree(3, count_override=99)
    return B.build_pdf(objs, root, mode="table")


def all_fixtures():
    return [(name, desc, fn()) for name, desc, fn in FIXTURES]
