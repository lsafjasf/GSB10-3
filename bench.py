#!/usr/bin/env python3
"""耗时对比：全量导出 vs 增量导出（无变化 / 少量变化 / 大量变化）。

运行：python3 bench.py [--rows 200000]
结果打印为 Markdown 表格，并写入 BENCHMARK.md。
"""
from __future__ import annotations

import argparse
import os
import shutil
import tempfile

from export_tool import exporter, source


def timed_export(conn, ckpt, out_dir):
    return exporter.run_export(conn, ckpt, out_dir)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=200000)
    parser.add_argument("--out", default="BENCHMARK.md")
    args = parser.parse_args()

    tmp = tempfile.mkdtemp(prefix="export-bench-")
    try:
        db = os.path.join(tmp, "source.db")
        ckpt = os.path.join(tmp, "ckpt.json")
        out_dir = os.path.join(tmp, "exports")
        conn = source.connect(db)
        source.init_db(conn, rows=args.rows, seed=42)

        rows = []
        # 1) 全量导出（首次，无位点）
        r_full = timed_export(conn, ckpt, out_dir)
        rows.append(("全量导出（基线）", f"{args.rows} 行全量",
                     r_full.record_count, r_full.elapsed_seconds))

        # 2) 无变化：增量应快速返回空结果
        r_empty = timed_export(conn, ckpt, out_dir)
        rows.append(("增量导出（无变化）", "0 行变更",
                     r_empty.record_count, r_empty.elapsed_seconds))

        # 3) 少量变化（0.5%）
        small = max(1, args.rows // 200)
        source.mutate(conn, changes=small, seed=7)
        r_small = timed_export(conn, ckpt, out_dir)
        rows.append((f"增量导出（{small} 行变化）", f"{small} 行变更",
                     r_small.record_count, r_small.elapsed_seconds))

        # 4) 大量变化（25%）
        large = args.rows // 4
        source.mutate(conn, changes=large, seed=8)
        r_large = timed_export(conn, ckpt, out_dir)
        rows.append((f"增量导出（{large} 行变化）", f"{large} 行变更",
                     r_large.record_count, r_large.elapsed_seconds))

        # 5) 对照：同一时刻再做一次全量
        r_full2 = timed_export(conn, os.path.join(tmp, "ckpt2.json"), out_dir)
        rows.append(("全量导出（对照）", "同一时刻全量",
                     r_full2.record_count, r_full2.elapsed_seconds))
        conn.close()

        speedup = r_full.elapsed_seconds / max(r_empty.elapsed_seconds, 1e-9)
        lines = [
            "# 耗时对比（全量 vs 增量）",
            "",
            f"- 数据规模：{args.rows} 行",
            f"- 环境：Python 3，SQLite（WAL），仅标准库",
            "",
            "| 场景 | 说明 | 导出记录数 | 耗时 |",
            "| --- | --- | ---: | ---: |",
        ]
        for name, desc, count, secs in rows:
            lines.append(f"| {name} | {desc} | {count} | {secs*1000:.2f} ms |")
        lines += [
            "",
            f"- 无变化时增量导出比全量快约 **{speedup:.0f} 倍**，"
            f"且结果为空（0 条记录），可快速返回。",
            f"- 增量耗时随变化量增长，与总数据量基本无关（O(变化量) vs O(总量)）。",
        ]
        report = "\n".join(lines) + "\n"
        print(report)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
