"""生成交付数据：
1) 空值比例 vs 压缩率关系（data/null_ratio_report.csv / .md）
2) 行存/列存查询对拍明细（data/diff_report.csv）
3) 边界情形编码结果（data/boundary_report.md）

运行：python3 gen_reports.py
"""

import csv
import os
import random

import columnar

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
N_ROW = 50000


def gen_null_ratio_report():
    """低基数列在不同空值比例下的行存/列存体积。"""
    rng = random.Random(100)
    ratios = [round(x * 0.05, 2) for x in range(21)]
    rows_out = []
    base_values = [rng.randint(0, 9) for _ in range(N_ROW)]  # 10 种取值
    for ratio in ratios:
        rng2 = random.Random(int(ratio * 1000) + 1)
        values = [None if rng2.random() < ratio else v for v in base_values]
        cols = ["val"]
        rows = [(v,) for v in values]
        rep = columnar.compression_report(cols, rows)
        blob = columnar.encode_table(cols, rows)
        h, _ = columnar._read_header(blob)
        rows_out.append({
            "null_ratio": "%.2f" % ratio,
            "null_count": values.count(None),
            "encoding": rep["columns"][0]["encoding"],
            "row_store_bytes": rep["row_store_bytes"],
            "columnar_bytes": rep["columnar_bytes"],
            "compression_ratio": "%.3f" % rep["compression_ratio"],
        })

    csv_path = os.path.join(DATA_DIR, "null_ratio_report.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
        w.writeheader()
        w.writerows(rows_out)

    md_path = os.path.join(DATA_DIR, "null_ratio_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# 空值比例 vs 压缩率（%d 行，低基数整数列，取值 0..9）\n\n" % N_ROW)
        f.write("- 行存基线：JSON Lines 序列化字节数\n")
        f.write("- 列存：空值位图 + 自适应编码(RLE/DICT/PLAIN) + 按列 zlib\n")
        f.write("- 压缩率 = 行存字节 / 列存字节，越大越好\n\n")
        f.write("| 空值比例 | 空值数 | 命中编码 | 行存字节 | 列存字节 | 压缩率 |\n")
        f.write("|---|---|---|---|---|---|\n")
        for r in rows_out:
            f.write("| %s | %s | %s | %s | %s | %s |\n" % (
                r["null_ratio"], r["null_count"], r["encoding"],
                r["row_store_bytes"], r["columnar_bytes"],
                r["compression_ratio"]))
    print("写入 %s" % csv_path)
    print("写入 %s" % md_path)
    return rows_out


def gen_diff_report():
    """固定数据集上的行存/列存查询对拍，逐行记录结果是否一致。"""
    rng = random.Random(202)
    cities = ["北京", "上海", "广州", None]
    columns = ["age", "city", "price", "vip"]
    rows = [
        (rng.randint(0, 100), rng.choice(cities),
         rng.choice([0.0, 9.9, 19.9, None]), rng.choice([True, False]))
        for _ in range(2000)
    ]
    blob = columnar.encode_table(columns, rows)

    # 三个查询：范围过滤 / 含空值等值过滤 / 空值判断
    queries = [
        ("age > 50 且 vip=True, 投影全部列",
         ("age", "city", "price", "vip"),
         lambda r: r["age"] > 50 and r["vip"], ("age", "vip")),
        ("city == None, 投影 city,age",
         ("city", "age"), lambda r: r["city"] is None, ("city",)),
        ("price 非空, 投影 price",
         ("price",), lambda r: r["price"] is not None, ("price",)),
    ]

    csv_path = os.path.join(DATA_DIR, "diff_report.csv")
    mismatch = 0
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["query", "row_no", "row_store_result",
                    "columnar_result", "match"])
        for qname, sel, where, where_cols in queries:
            got_row = columnar.row_query(columns, rows, sel, where)
            got_col = columnar.col_query(blob, sel, where, where_cols)
            assert len(got_row) == len(got_col)
            for i, (a, b) in enumerate(zip(got_row, got_col)):
                ok = (a == b)
                mismatch += 0 if ok else 1
                w.writerow([qname, i, repr(a), repr(b), "OK" if ok else "DIFF"])
    print("写入 %s（对拍行数=%d，不一致=%d）" % (csv_path, sum(
        len(columnar.row_query(columns, rows, s, w)) for _, s, w, _ in queries),
        mismatch))
    assert mismatch == 0
    return mismatch


def gen_boundary_report():
    """四种边界情形 + 两种补充情形的编码方式与压缩率。"""
    n = 20000
    rng = random.Random(9)
    cases = [
        ("全空列", [(None,) for _ in range(n)]),
        ("单值列", [("CONST",) for _ in range(n)]),
        ("高度重复列(4值乱序)",
         [(rng.choice(["A", "B", "C", "D"]),) for _ in range(n)]),
        ("混合类型列",
         [([1, 2.5, "x", True, None][i % 5],) for i in range(n)]),
        ("全列无空值/高基数", [(rng.randint(0, n),) for _ in range(n)]),
        ("50%空值+低基数",
         [(None if rng.random() < 0.5 else rng.randint(0, 9),)
          for _ in range(n)]),
    ]
    md_path = os.path.join(DATA_DIR, "boundary_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# 边界情形编码结果（各 %d 行，单列）\n\n" % n)
        f.write("| 情形 | 空值比例 | 命中编码 | 行存字节 | 列存字节 | 压缩率 | 逐行还原 |\n")
        f.write("|---|---|---|---|---|---|---|\n")
        for name, rows in cases:
            cols = ["v"]
            rep = columnar.compression_report(cols, rows)
            # 逐行还原验证
            _, decoded = columnar.decode_table(columnar.encode_table(cols, rows))
            ok = decoded == [tuple(r) for r in rows]
            f.write("| %s | %.2f | %s | %d | %d | %.2f | %s |\n" % (
                name, rep["columns"][0]["null_ratio"],
                rep["columns"][0]["encoding"], rep["row_store_bytes"],
                rep["columnar_bytes"], rep["compression_ratio"],
                "OK" if ok else "FAIL"))
    print("写入 %s" % md_path)


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    report = gen_null_ratio_report()
    print("\n空值比例 -> 压缩率（节选）:")
    for r in report:
        if r["null_ratio"] in ("0.00", "0.25", "0.50", "0.75", "1.00"):
            print("  null=%s 编码=%-5s 行存=%7s 列存=%6s 压缩率=%s" % (
                r["null_ratio"], r["encoding"], r["row_store_bytes"],
                r["columnar_bytes"], r["compression_ratio"]))
    gen_diff_report()
    gen_boundary_report()
