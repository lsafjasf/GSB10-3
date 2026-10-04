#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pdf_tool：PDF 对象定位与解析命令行工具（仅标准库）。

子命令：
  inspect FILE   对象清单 + 页面顺序 + 压缩对象映射 + 页面树校验
  verify  FILE   索引声明与顺序扫描重建结果对拍
  repair  FILE -o OUT  追加干净索引段，生成修复文件
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pdflite import Document
from pdflite.objects import PdfStream
from pdflite import verify as verify_mod
from pdflite import repair as repair_mod

MODE_CN = {"table": "传统偏移表", "stream": "流式索引(XRef stream)",
           "mixed": "混合(增量更新)", "none": "无索引"}


def _brief(value, limit=48):
    if isinstance(value, PdfStream):
        return "PdfStream(%d bytes)" % len(value.raw)
    s = repr(value)
    return s if len(s) <= limit else s[:limit - 3] + "..."


def cmd_inspect(path):
    doc = Document.load(path)
    print("文件: %s (%d 字节)" % (path, len(doc.data)))
    print("PDF 版本: %s" % (doc.version or "未知"))
    print("索引形态: %s，索引段数: %d，顺序重建: %s" % (
        MODE_CN.get(doc.index_mode, doc.index_mode), len(doc.sections),
        "是" if doc.recovered else "否"))

    print("\n== 对象清单 ==")
    print("%-6s %-6s %-10s %-9s %s" % ("编号", "代", "偏移", "类别", "内容摘要"))
    for num in sorted(doc.objects):
        obj = doc.objects[num]
        if obj.stream_num >= 0:
            cat = "压缩"
            loc = "流%d#%d" % (obj.stream_num, obj.stream_index)
        else:
            cat = "普通"
            loc = str(obj.offset)
        print("%-6d %-6d %-10s %-9s %s" % (
            num, obj.gen, loc, cat, _brief(obj.value)))

    if doc.compressed:
        print("\n== 对象流映射（流内序号 -> 全局编号）==")
        for stm, pairs in sorted(doc.compressed_pairs().items()):
            body = ", ".join("#%d->%d" % (idx, num) for idx, num in pairs)
            print("对象流 %d: %s" % (stm, body))

    pages, nodes, errors = doc.pages()
    print("\n== 页面顺序 ==")
    if not pages:
        print("(无页面)")
    for p in pages:
        if p.inline:
            loc = "内联"
        elif p.in_object_stream:
            loc = "压缩于对象流 %d，流内序号 %d" % (p.stream_num, p.stream_index)
        else:
            loc = "偏移 %d" % doc.objects[p.num].offset
        print("第 %d 页: 对象 %d %d R (%s)" % (p.ordinal, p.num, p.gen, loc))

    print("\n== 页面树校验 ==")
    for node in nodes:
        label = "内联" if node.inline else (
            "%d %d R" % (node.ref.num, node.ref.gen) if node.ref is not None else "?")
        if node.kind == "/Pages":
            mark = "一致" if node.declared_count == node.actual_count else "不一致!"
            print("节点 %s [/Pages] 声明 /Count=%s 实际展开=%d 子节点=%d -> %s" % (
                label, node.declared_count, node.actual_count, node.kids, mark))
        else:
            print("节点 %s [%s] (叶子)" % (label, node.kind))
    if not nodes:
        print("(页面树不可用)")

    if doc.warnings or errors:
        print("\n== 警告与问题 ==")
        for w in doc.warnings:
            print("[警告] %s" % w)
        for e in errors:
            print("[校验] %s" % e)
    return 0


def cmd_verify(path, strict=False):
    doc = Document.load(path)
    rep = verify_mod.compare(doc)
    print(verify_mod.format_report(rep))
    if strict and not doc.index_ok:
        return 2
    return 0 if rep.ok else 1


def cmd_repair(path, out_path):
    doc = Document.load(path)
    data = repair_mod.repaired_bytes(doc)
    with open(out_path, "wb") as fh:
        fh.write(data)
    print("已写出修复文件: %s (%d 字节，原 %d 字节)" % (out_path, len(data), len(doc.data)))
    # 自检：修复后的文件应能被索引正常加载
    doc2 = Document(data)
    ok = doc2.index_ok and not doc2.recovered
    print("修复自检: %s" % ("通过" if ok else "未通过"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pdf_tool", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("inspect", help="对象清单与页面顺序")
    p1.add_argument("file")
    p2 = sub.add_parser("verify", help="索引与扫描重建对拍")
    p2.add_argument("file")
    p2.add_argument("--strict", action="store_true",
                    help="索引不可用时也视为失败")
    p3 = sub.add_parser("repair", help="追加干净索引段生成修复文件")
    p3.add_argument("file")
    p3.add_argument("-o", "--out", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "inspect":
        return cmd_inspect(args.file)
    if args.cmd == "verify":
        return cmd_verify(args.file, args.strict)
    if args.cmd == "repair":
        return cmd_repair(args.file, args.out)
    return 2


if __name__ == "__main__":
    sys.exit(main())
