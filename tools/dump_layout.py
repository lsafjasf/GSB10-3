"""打印 examples 中布局的 Markdown 参考表（docs/layout_reference.md 由它生成）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from binlayout import compute_layout, layout_table  # noqa: E402
import examples  # noqa: E402


def render(desc):
    layout = compute_layout(desc)
    lines = [
        f"### `{layout.name}`（总宽 {layout.size}，对齐 {layout.align}，"
        f"末尾补齐 {layout.tail_padding}）",
        "",
        "| 字段 | 偏移 | 宽度 | 对齐 | 前置填充 | 备注 |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in layout_table(layout):
        lines.append(
            f"| `{row['path']}` | {row['offset']} | {row['size']} "
            f"| {row['align']} | {row['pad_before']} | {row['note']} |")
    return "\n".join(lines)


if __name__ == "__main__":
    print(render(examples.inner))
    print()
    print(render(examples.packet))
