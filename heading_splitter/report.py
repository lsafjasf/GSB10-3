"""对照数据与报告的渲染（纯文本表 + JSON 导出）。"""

from __future__ import annotations

import json
from dataclasses import asdict

from .models import SplitResult
from .splitter import DEMOTE, FILL


def _table(headers, rows) -> str:
    widths = [len(h) for h in headers]
    str_rows = []
    for row in rows:
        cells = [str(c) for c in row]
        str_rows.append(cells)
        widths = [max(w, len(c)) for w, c in zip(widths, cells)]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    sep = "  ".join("-" * w for w in widths)
    body = ["  ".join(c.ljust(w) for c, w in zip(row, widths)) for row in str_rows]
    return "\n".join([line, sep, *body])


def render_comparison(result: SplitResult) -> str:
    """层级处理前后对照（两种修复策略）。"""
    chunks = []
    for strategy, label in ((FILL, "补齐策略 fill"), (DEMOTE, "降级策略 demote")):
        rows = []
        for rec in result.comparisons[strategy]:
            rows.append([
                rec.block_index,
                "是" if rec.synthetic else "否",
                rec.text[:28],
                f"L{rec.prev_level}->L{rec.raw_level}",
                f"L{rec.final_level}",
                rec.action,
            ])
        chunks.append(f"## {label}\n" + _table(
            ["块", "虚拟", "标题", "处理前(上层->原始)", "处理后", "处理动作"], rows))
    return "\n\n".join(chunks)


def render_report(result: SplitResult) -> str:
    parts = [
        f"正文字号: {result.body_size}pt  正文缩进: {result.body_indent}pt",
        f"字号档位(大->小): {result.size_tiers}  缩进档位(左->右): {result.indent_tiers}",
        f"前置内容块区间: {result.preamble}",
        "",
        "== 标题判定与线索取舍 ==",
    ]
    rows = []
    for heading in result.headings:
        votes = "；".join(f"{src}=L{v['level']}(w={v['weight']})"
                          for src, v in heading.votes.items())
        rows.append([heading.block_index, heading.text[:24], f"L{heading.raw_level}",
                     votes, heading.reason])
    parts.append(_table(["块", "标题", "原始层级", "各线索投票", "取舍依据"], rows))
    if result.suppressed:
        parts.append("\n== 被抑制的疑似标题 ==")
        parts.append(_table(["块", "文本", "原因"],
                            [[s["block_index"], s["text"][:24], s["reason"]]
                             for s in result.suppressed]))
    parts.append("\n== 层级跳级处理对照 ==")
    parts.append(render_comparison(result))
    parts.append("\n== 章节区间（补齐策略，[start, end)）==")
    parts.append(_table(
        ["层级", "标题块", "区间"],
        [[f"L{lv}", idx, f"[{start}, {end})"] for lv, idx, start, end in result.intervals]))
    return "\n".join(parts)


def report_dict(result: SplitResult) -> dict:
    """可 JSON 序列化的对照数据。"""
    return {
        "body_size": result.body_size,
        "body_indent": result.body_indent,
        "size_tiers": result.size_tiers,
        "indent_tiers": result.indent_tiers,
        "preamble": list(result.preamble),
        "headings": [
            {
                "block_index": h.block_index,
                "text": h.text,
                "raw_level": h.raw_level,
                "votes": h.votes,
                "reason": h.reason,
            }
            for h in result.headings
        ],
        "suppressed": result.suppressed,
        "comparisons": {
            strategy: [asdict(rec) for rec in records]
            for strategy, records in result.comparisons.items()
        },
        "intervals": [
            {"level": lv, "heading_block": idx, "start": start, "end": end}
            for lv, idx, start, end in result.intervals
        ],
    }


def report_json(result: SplitResult) -> str:
    return json.dumps(report_dict(result), ensure_ascii=False, indent=2)
