#!/usr/bin/env python3
"""自测入口：生成示例 -> 切分 -> 逐字符重建断言 -> 输出对照数据。

用法（在仓库根目录）：
    python3 run_selftest.py
产物：
    examples/demo.blocks.txt   示例文档规范文本
    examples/level_report.json 完整判定与对照数据（机器可读）
    examples/comparison.md     层级处理前后对照（人读）
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from examples.demo_doc import build_demo_blocks
from heading_splitter import (
    DEMOTE,
    FILL,
    dump_source,
    parse_source,
    rebuild_source,
    render_comparison,
    render_report,
    split_document,
)

HERE = os.path.dirname(os.path.abspath(__file__))
EX = os.path.join(HERE, "examples")


def main() -> int:
    blocks = build_demo_blocks()
    source = dump_source(blocks)

    # 1) 规范文本自身的逐字符往返
    reparsed = parse_source(source)
    assert reparsed == blocks, "规范文本往返后块不一致"
    assert dump_source(reparsed) == source, "规范文本二次序列化不逐字符一致"

    # 2) 切分
    result = split_document(reparsed)

    # 3) 重建断言（核心不变量）：两种修复策略都必须逐字符还原
    rebuilt_fill = rebuild_source(result, FILL)
    rebuilt_demote = rebuild_source(result, DEMOTE)
    assert rebuilt_fill == source, "补齐策略重建结果与原文不逐字符一致"
    assert rebuilt_demote == source, "降级策略重建结果与原文不逐字符一致"

    # 4) 区间划分断言：前置区间 + 各章节区间恰好覆盖 [0, N)，不重不漏
    covered = set()
    start, first_end = result.preamble
    covered.update(range(start, first_end))
    for _level, _idx, lo, hi in result.intervals:
        assert lo < hi
        chunk = set(range(lo, hi))
        assert not (chunk & covered), f"章节区间重叠: [{lo}, {hi})"
        covered |= chunk
    assert covered == set(range(len(blocks))), "章节区间未完整覆盖所有块"

    # 5) 落盘示例与对照数据
    os.makedirs(EX, exist_ok=True)
    with open(os.path.join(EX, "demo.blocks.txt"), "w", encoding="utf-8", newline="") as f:
        f.write(source)
    from heading_splitter.report import report_dict
    with open(os.path.join(EX, "level_report.json"), "w", encoding="utf-8") as f:
        json.dump(report_dict(result), f, ensure_ascii=False, indent=2)
        f.write("\n")
    comparison_md = (
        "# 层级处理前后对照（示例文档）\n\n"
        + render_comparison(result)
        + "\n\n## 重建断言\n\n"
        + "- 补齐策略重建 == 原文（逐字符）：通过\n"
        + "- 降级策略重建 == 原文（逐字符）：通过\n"
        + f"- 原文长度 {len(source)} 字符，共 {len(blocks)} 个块\n"
    )
    with open(os.path.join(EX, "comparison.md"), "w", encoding="utf-8") as f:
        f.write(comparison_md)

    print(render_report(result))
    print()
    print("=" * 72)
    print(f"重建断言通过：两种策略重建均与原文逐字符一致（{len(source)} 字符 / {len(blocks)} 块）")
    print(f"产物已写入：{os.path.relpath(EX, HERE)}/demo.blocks.txt, level_report.json, comparison.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
