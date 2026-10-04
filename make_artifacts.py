#!/usr/bin/env python3
"""生成可人工核对的对拍数据（data/duipai/）。

流程：全量基线 -> 随机变更 -> 增量导出 -> 同一时刻再全量
     -> 基线应用增量 -> 与全量逐行 diff（期望 0 处不一致）
"""
from __future__ import annotations

import json
import os
import shutil

from export_tool import exporter, reconcile, source

ART_DIR = os.path.join("data", "duipai")
ROWS = 10000
CHANGES = 2000


def main() -> None:
    shutil.rmtree(ART_DIR, ignore_errors=True)
    os.makedirs(ART_DIR, exist_ok=True)
    work = os.path.join(ART_DIR, "_work")
    os.makedirs(work, exist_ok=True)
    db = os.path.join(work, "source.db")
    ckpt = os.path.join(work, "ckpt.json")
    ckpt2 = os.path.join(work, "ckpt_full2.json")
    out_dir = os.path.join(work, "exports")

    conn = source.connect(db)
    source.init_db(conn, rows=ROWS, seed=42)

    # t0：全量基线
    base = exporter.run_export(conn, ckpt, out_dir)
    base_copy = os.path.join(ART_DIR, "full_t0.jsonl")
    shutil.copy2(base.path, base_copy)

    # 变更 2000 次（更新/删除/新增混合）
    stats = source.mutate(conn, changes=CHANGES, seed=2026)

    # t1：增量导出（位点 t0 -> t1）
    inc = exporter.run_export(conn, ckpt, out_dir)
    inc_copy = os.path.join(ART_DIR, "inc_t0_t1.jsonl")
    shutil.copy2(inc.path, inc_copy)

    # t1：同一时刻（无并发写入）再做全量
    full2 = exporter.run_export(conn, ckpt2, out_dir)
    full2_copy = os.path.join(ART_DIR, "full_t1.jsonl")
    shutil.copy2(full2.path, full2_copy)

    # 对拍：基线全量 + 增量 == t1 全量
    state = reconcile.load_state(base_copy)
    reconcile.apply_incremental(state, inc_copy)
    expected = reconcile.load_state(full2_copy)
    problems = reconcile.diff_states(state, expected)

    reconciled_path = os.path.join(ART_DIR, "reconciled_t1.jsonl")
    with open(reconciled_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "type": "full", "from_version": 0,
            "to_version": inc.to_version}, ensure_ascii=False) + "\n")
        for rid in sorted(state):
            rec = state[rid]
            f.write(json.dumps({
                "id": rid, "payload": rec["payload"],
                "version": rec["version"], "deleted": 0},
                ensure_ascii=False) + "\n")

    changed_rows = conn.execute(
        "SELECT COUNT(*) FROM records WHERE version > ?",
        (base.to_version,)).fetchone()[0]
    conn.close()

    report_lines = [
        "# 对拍报告（基线全量 + 增量 vs 同一时刻全量）",
        "",
        f"- 初始行数: {ROWS}",
        f"- 变更操作数: {CHANGES}，其中 {stats}",
        f"- 越过位点的行数（增量文件记录数，含墓碑）: {changed_rows}",
        f"- 位点区间: {inc.from_version} -> {inc.to_version}",
        f"- 基线全量文件: full_t0.jsonl（{base.record_count} 行存活）",
        f"- 增量文件: inc_t0_t1.jsonl（{inc.record_count} 条，含删除墓碑）",
        f"- t1 全量文件: full_t1.jsonl（{full2.record_count} 行存活）",
        f"- 应用增量后状态: reconciled_t1.jsonl（{len(state)} 行存活）",
        f"- 不一致数量: {len(problems)}",
        "",
        "结论: " + ("对拍通过，两种导出在 t1 时刻严格等价。"
                  if not problems else f"对拍失败: {problems[:5]}"),
    ]
    with open(os.path.join(ART_DIR, "diff_report.txt"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(report_lines) + "\n")

    shutil.rmtree(work, ignore_errors=True)
    print("\n".join(report_lines))


if __name__ == "__main__":
    main()
