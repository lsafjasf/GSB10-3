"""基准与对拍数据生成。

运行：python3 benchmarks.py
产出：
  data/null_ratio_compression.csv  空值比例 vs 压缩率
  data/query_diff_report.txt       行存/列存查询逐行对拍 + 全表还原对拍
"""

import csv
import os
import random

from columnar import ColumnarTable, measure, normalize_rows, query_columnar, query_rows

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
N = 20000


def null_ratio_experiment():
    """单列 20000 行，空值比例 0%..100%，统计列存/行存体积比。"""
    rng = random.Random(20261004)
    ratios = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 1.0]
    base_int = [rng.randint(0, 99) for _ in range(N)]
    base_str = [rng.choice(["alpha", "beta", "gamma", "delta"]) for _ in range(N)]

    path = os.path.join(DATA_DIR, "null_ratio_compression.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["scenario", "null_ratio", "rows", "row_bytes",
                    "columnar_bytes", "ratio_col_over_row", "saving_pct",
                    "row_zlib_bytes", "columnar_zlib_bytes", "zlib_ratio"])
        for scenario, base in (("int_0_99", base_int), ("str_4_values", base_str)):
            for p in ratios:
                rows = [{"v": (None if rng.random() < p else v)} for v in base]
                m = measure(rows)
                w.writerow([scenario, p, m["rows"], m["row_bytes"],
                            m["columnar_bytes"], round(m["ratio"], 4),
                            round(m["saving"] * 100, 2),
                            m["row_zlib_bytes"], m["columnar_zlib_bytes"],
                            round(m["zlib_ratio"], 4)])
    print("written:", path)
    # 控制台也打一份，方便直接看趋势
    with open(path, encoding="utf-8") as f:
        for line in f:
            print("  " + line.rstrip())


def build_edge_case_table():
    """覆盖：全空列、单值列、高度重复列、混合类型列 + 普通列。"""
    rng = random.Random(7)
    rows = []
    for i in range(5000):
        rows.append({
            "id": i,
            "all_null": None,
            "single": "CONST",
            "repeat": "hot" if rng.random() < 0.95 else rng.choice(["warm", "cold"]),
            "mixed": rng.choice([1, True, 1.5, "x", None, False, "1"]),
            "score": rng.choice([None, rng.randint(0, 100)]),
        })
    return rows


def query_diff_report():
    rows = build_edge_case_table()
    table = ColumnarTable.from_rows(rows)

    lines = []
    lines.append("=== 全表还原对拍（列存解码 vs 原始行存） ===")
    expected, _ = normalize_rows(rows)
    restored = table.to_rows()
    mismatch = sum(1 for a, b in zip(expected, restored) if a != b)
    lines.append("rows=%d restored=%d mismatched_rows=%d -> %s"
                 % (len(rows), len(restored), mismatch,
                    "PASS" if mismatch == 0 and len(restored) == len(rows) else "FAIL"))
    lines.append("")

    queries = [
        ("全表扫描", ["id", "all_null", "single", "repeat", "mixed", "score"], []),
        ("全空列 is_null", ["id"], [("all_null", "is_null", None)]),
        ("全空列等值(应空集)", ["id"], [("all_null", "==", 1)]),
        ("单值列等值命中", ["id"], [("single", "==", "CONST")]),
        ("单值列等值未命中", ["id"], [("single", "==", "OTHER")]),
        ("高度重复列等值", ["id"], [("repeat", "==", "hot")]),
        ("高度重复列不等", ["id"], [("repeat", "!=", "hot")]),
        ("混合列 bool 等值", ["id"], [("mixed", "==", True)]),
        ("混合列 int 等值(不吃 bool)", ["id"], [("mixed", "==", 1)]),
        ("混合列字符串等值", ["id"], [("mixed", "==", "1")]),
        ("混合列 is_null", ["id"], [("mixed", "is_null", None)]),
        ("范围过滤", ["id", "score"], [("score", ">=", 50), ("score", "<", 80)]),
        ("范围+空值组合", ["id"], [("score", "not_null", None), ("repeat", "==", "hot")]),
        ("空结果集", ["id"], [("id", ">", 10**9)]),
    ]

    lines.append("=== 查询对拍（行存结果 vs 列存结果，逐行比较） ===")
    lines.append("%-28s %8s %8s %s" % ("query", "row_cnt", "col_cnt", "result"))
    all_pass = True
    for name, select, filters in queries:
        got_row = query_rows(rows, select, filters)
        got_col = query_columnar(table, select, filters)
        ok = got_row == got_col
        all_pass &= ok
        lines.append("%-28s %8d %8d %s"
                     % (name, len(got_row), len(got_col), "MATCH" if ok else "DIFF"))
    lines.append("")
    lines.append("overall: %s" % ("PASS" if all_pass and mismatch == 0 else "FAIL"))

    path = os.path.join(DATA_DIR, "query_diff_report.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("written:", path)
    print("\n".join(lines))


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    null_ratio_experiment()
    print()
    query_diff_report()


if __name__ == "__main__":
    main()
