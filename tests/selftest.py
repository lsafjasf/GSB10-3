#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自测：对象定位、索引解析、扫描重建、对拍、页面树校验、边界情形。

运行: python3 tests/selftest.py
退出码 0 表示全部通过。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pdflite import Document
from pdflite.objects import Name, PdfStream
from pdflite import verify as verify_mod
from pdflite import repair as repair_mod
from tests.fixtures import all_fixtures

FAILED = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print("[%s] %s%s" % (status, name, (" -- " + detail) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def page_nums(doc):
    pages, nodes, errors = doc.pages()
    return [p.num for p in pages], nodes, errors


def get(fixtures, prefix):
    for name, desc, data in fixtures:
        if name.startswith(prefix):
            return data
    raise KeyError(prefix)


def main():
    fixtures = all_fixtures()

    # ---- 01 传统偏移表 ----
    doc = Document(get(fixtures, "01"))
    nums, nodes, errors = page_nums(doc)
    check("01 索引形态为传统表", doc.index_mode == "table")
    check("01 页面顺序 [3,4,5]", nums == [3, 4, 5], str(nums))
    check("01 无校验错误", not errors, str(errors))
    check("01 伪对象头被过滤(对象6是流)", isinstance(doc.objects[6].value, PdfStream))
    check("01 扫描不误报伪对象", all(r.num != 123 for r in doc.scan_map.values()))
    rep = verify_mod.compare(doc)
    check("01 对拍通过", rep.ok and all(r.status == "match" for r in rep.rows))

    # ---- 02 流式索引 + 两级页面树 ----
    doc = Document(get(fixtures, "02"))
    nums, nodes, errors = page_nums(doc)
    check("02 索引形态为流式", doc.index_mode == "stream")
    check("02 深度优先页面顺序 [4,5,7,8]", nums == [4, 5, 7, 8], str(nums))
    pages_nodes = [n for n in nodes if n.kind == "/Pages"]
    check("02 三个 /Pages 节点 /Count 全部一致",
          len(pages_nodes) == 3 and
          all(n.declared_count == n.actual_count for n in pages_nodes))
    rep = verify_mod.compare(doc)
    check("02 对拍通过", rep.ok)

    # ---- 03 对象流 ----
    doc = Document(get(fixtures, "03"))
    nums, nodes, errors = page_nums(doc)
    check("03 压缩对象映射 {6:[(0,3),(1,4),(2,5)]}",
          doc.compressed_pairs() == {6: [(0, 3), (1, 4), (2, 5)]},
          str(doc.compressed_pairs()))
    check("03 压缩页按序解压 [3,4,5]", nums == [3, 4, 5], str(nums))
    pages, _, _ = doc.pages()
    check("03 页面携带流内序号",
          [(p.stream_num, p.stream_index) for p in pages] == [(6, 0), (6, 1), (6, 2)])
    labels = [doc.objects[n].value[Name("/Label")] for n in (3, 4, 5)]
    check("03 压缩对象内容正确", labels == [b"A", b"B", b"C"], str(labels))
    rep = verify_mod.compare(doc)
    check("03 对拍通过(含压缩条目)", rep.ok)

    # ---- 04 增量更新(表->表) ----
    doc = Document(get(fixtures, "04"))
    nums, nodes, errors = page_nums(doc)
    check("04 两个索引段", len(doc.sections) == 2)
    check("04 对象 2 被覆盖", doc.overridden() == [2], str(doc.overridden()))
    check("04 新定义生效: 页面 [3,4,5]", nums == [3, 4, 5], str(nums))
    kids = doc.objects[2].value[Name("/Kids")]
    check("04 覆盖后 Kids 含 3 项", len(kids) == 3)
    rep = verify_mod.compare(doc)
    check("04 对拍通过(旧条目指向历史版本)", rep.ok)

    # ---- 05 增量更新(表->流混合) ----
    doc = Document(get(fixtures, "05"))
    nums, nodes, errors = page_nums(doc)
    check("05 混合索引形态", doc.index_mode == "mixed")
    check("05 页面 [3,4]", nums == [3, 4], str(nums))
    rep = verify_mod.compare(doc)
    check("05 对拍通过", rep.ok)

    # ---- 06 偏移全部失效 ----
    doc = Document(get(fixtures, "06"))
    nums, nodes, errors = page_nums(doc)
    check("06 索引可解析但条目失效", doc.index_ok and not doc.recovered)
    check("06 失效条目全部经扫描修复",
          all(not o.header_ok or o.offset >= 0 for o in doc.objects.values()))
    check("06 修复后页面 [3,4,5]", nums == [3, 4, 5], str(nums))
    rep = verify_mod.compare(doc)
    rows = {r.status for r in rep.rows}
    check("06 对拍标记为已修复", "repaired" in rows and rep.ok, str(rows))

    # ---- 07 startxref 失效 ----
    doc = Document(get(fixtures, "07"))
    nums, nodes, errors = page_nums(doc)
    check("07 回退到顺序重建", doc.recovered)
    check("07 重建后页面 [3,4,5]", nums == [3, 4, 5], str(nums))

    # ---- 08 尾部截断(传统表) ----
    doc = Document(get(fixtures, "08"))
    nums, nodes, errors = page_nums(doc)
    check("08 截断后回退重建", doc.recovered)
    check("08 无索引仍恢复页面 [3,4,5]", nums == [3, 4, 5], str(nums))
    check("08 无校验错误", not errors, str(errors))

    # ---- 09 尾部截断(流式索引，XRef 流幸存) ----
    doc = Document(get(fixtures, "09"))
    nums, nodes, errors = page_nums(doc)
    check("09 startxref 丢失触发重建", doc.recovered)
    check("09 从扫描到的 XRef 流恢复压缩映射",
          doc.compressed_pairs() == {6: [(0, 3), (1, 4), (2, 5)]},
          str(doc.compressed_pairs()))
    check("09 压缩页面全部恢复 [3,4,5]", nums == [3, 4, 5], str(nums))

    # ---- 10 /Count 不符 ----
    doc = Document(get(fixtures, "10"))
    nums, nodes, errors = page_nums(doc)
    check("10 页面仍按实际展开 [3,4,5]", nums == [3, 4, 5], str(nums))
    check("10 报出 /Count 不一致", any("/Count" in e for e in errors), str(errors))

    # ---- 修复写回：06/07/08 修复后可被索引正常加载 ----
    for prefix in ("06", "07", "08"):
        doc = Document(get(fixtures, prefix))
        data2 = repair_mod.repaired_bytes(doc)
        doc2 = Document(data2)
        nums2, _, errs2 = page_nums(doc2)
        check("%s 修复后索引可用且页面一致" % prefix,
              doc2.index_ok and not doc2.recovered and nums2 == [3, 4, 5] and not errs2,
              "recovered=%s nums=%s errs=%s" % (doc2.recovered, nums2, errs2))

    # ---- 修复写回：09 含压缩对象，走 XRef 流修复 ----
    doc = Document(get(fixtures, "09"))
    data2 = repair_mod.repaired_bytes(doc)
    doc2 = Document(data2)
    nums2, _, errs2 = page_nums(doc2)
    check("09 修复后压缩映射保留",
          doc2.index_ok and doc2.compressed_pairs() == {6: [(0, 3), (1, 4), (2, 5)]}
          and nums2 == [3, 4, 5],
          "pairs=%s nums=%s" % (doc2.compressed_pairs(), nums2))

    print()
    if FAILED:
        print("失败 %d 项: %s" % (len(FAILED), ", ".join(FAILED)))
        return 1
    print("全部自测通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
