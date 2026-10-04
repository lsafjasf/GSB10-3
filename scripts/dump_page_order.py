#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""输出各夹具的页面顺序清单（Markdown），供人工核对。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pdflite import Document
from tests.fixtures import all_fixtures


def main():
    lines = ["# 页面顺序清单", "",
             "由 `scripts/dump_page_order.py` 依据实际展开结果生成，",
             "`python3 scripts/dump_page_order.py` 可随时重新生成。", ""]
    for name, desc, data in fixtures:
        doc = Document(data)
        pages, nodes, errors = doc.pages()
        lines.append("## %s" % name)
        lines.append("")
        lines.append("- 情形：%s" % desc)
        lines.append("- 索引形态：%s；段数：%d；扫描重建：%s" % (
            doc.index_mode, len(doc.sections), "是" if doc.recovered else "否"))
        if pages:
            lines.append("- 页面顺序（共 %d 页）：" % len(pages))
            for p in pages:
                if p.inline:
                    loc = "内联字典"
                elif p.in_object_stream:
                    loc = "压缩于对象流 %d，流内序号 %d" % (p.stream_num, p.stream_index)
                else:
                    loc = "偏移 %d" % doc.objects[p.num].offset
                lines.append("    1. 第 %d 页 ← 对象 %d %d R（%s）" % (
                    p.ordinal, p.num, p.gen, loc))
        else:
            lines.append("- 页面顺序：（无可用页面）")
        count_notes = []
        for n in nodes:
            if n.kind == "/Pages":
                count_notes.append("%s声明=%s/实际=%d" % (
                    "一致 " if n.declared_count == n.actual_count else "不一致 ",
                    n.declared_count, n.actual_count))
        if count_notes:
            lines.append("- /Count 校验：%s" % "；".join(count_notes))
        if errors:
            lines.append("- 校验问题：%s" % "；".join(errors))
        if doc.overridden():
            lines.append("- 被覆盖对象：%s" % doc.overridden())
        if doc.compressed_pairs():
            mapping = []
            for stm, pairs in sorted(doc.compressed_pairs().items()):
                mapping.append("对象流 %d：%s" % (
                    stm, ", ".join("#%d→%d" % (i, n) for i, n in pairs)))
            lines.append("- 压缩映射：%s" % "；".join(mapping))
        lines.append("")
    print("\n".join(lines))
    return 0


fixtures = all_fixtures()

if __name__ == "__main__":
    sys.exit(main())
