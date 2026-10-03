"""演示：边界推断、跨列/跨行合并前后对照、列数一致性校验。

运行：python3 demo.py
"""

from tableparse import parse_table, format_grid

# 样例 1：跨列合并（“待分配”越过部门/职级之间的边界空白带）、
# 跨行合并标记（“同上”）、尾部空单元格（赵六的备注为空）。
SAMPLE_1 = """
姓名    部门    职级     备注
张三    研发    T5       全勤
王五    市场    T4       同上
李四    待分配           新入职
赵六    市场    T6
"""

# 样例 2：垂直居中的跨行合并踪迹（“研发中心”在 3 行垂直居中）。
SAMPLE_2 = """
部门      姓名  季度
          甲    Q1
研发中心  乙    Q2
          丙    Q3
"""


def show(title, text, **kwargs):
    table = parse_table(text, **kwargs)

    print("=" * 64)
    print(title)
    print("=" * 64)
    print("原始文本：")
    for i, line in enumerate(l for l in text.splitlines() if l.strip()):
        print(f"  {i}: {line}")

    print("\n推断的列边界空白带（显示列区间）:", table.bands)
    print("推断的各列显示列区间           :", table.columns)

    print("\n【合并前】仅按推断边界切分：")
    print(format_grid(table.raw_rows))

    print("\n合并操作（前 -> 后）：")
    for m in table.merges:
        if m.kind == "colspan":
            r, c = m.anchor
            print(f"  跨列: 第{r}行 第{c}~{c + m.span[1] - 1}列 "
                  f"{m.before} -> {m.after!r}")
        else:
            r, c = m.anchor
            end = r + m.span[0] - 1
            print(f"  跨行: 第{r}~{end}行 第{c}列 "
                  f"{m.before} -> {m.after!r}（锚点 ({r},{c})）")

    print("\n【合并后】矩形网格（── 表示被合并覆盖的单元格）：")
    print(format_grid(table.rows))

    lengths = {len(row) for row in table.rows}
    print(f"\n每行列数: {sorted(lengths)}（共 {table.n_columns} 列，"
          f"每行 {len(table.rows[0])} 列）")
    print("一致性校验:", table.problems if table.problems else "全部一致 ✓")
    print()


if __name__ == "__main__":
    show("样例 1：跨列合并 + 跨行标记 + 空单元格", SAMPLE_1)
    show("样例 2：垂直居中的跨行合并", SAMPLE_2, centered_rowspan=True)
