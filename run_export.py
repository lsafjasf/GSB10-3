#!/usr/bin/env python3
"""导出工具命令行入口（仅依赖 Python 3 标准库）。

用法：
  python3 run_export.py init     --db data/source.db --rows 100000
  python3 run_export.py mutate   --db data/source.db --changes 2000
  python3 run_export.py export   --db data/source.db --checkpoint data/ckpt.json --out-dir data/exports
  python3 run_export.py verify   --base data/exports/full_xxx.jsonl \\
      --incremental data/exports/inc_vA_vB.jsonl --full data/exports/full_yyy.jsonl
"""
from __future__ import annotations

import argparse
import json

from export_tool import exporter, reconcile, source


def cmd_init(args) -> None:
    conn = source.connect(args.db)
    try:
        source.init_db(conn, rows=args.rows, payload_size=args.payload_size,
                       seed=args.seed)
        print(f"初始化完成：{args.rows} 行，当前位点版本 {source.current_seq(conn)}")
    finally:
        conn.close()


def cmd_mutate(args) -> None:
    conn = source.connect(args.db)
    try:
        stats = source.mutate(conn, changes=args.changes, seed=args.seed)
        print(f"变更完成：{stats}，当前位点版本 {source.current_seq(conn)}")
    finally:
        conn.close()


def cmd_export(args) -> None:
    conn = source.connect(args.db)
    try:
        result = exporter.run_export(conn, args.checkpoint, args.out_dir)
    finally:
        conn.close()
    label = "全量" if result.kind == exporter.FULL else "增量"
    print(f"导出类型: {label}")
    if result.fallback_reason:
        print(f"回退原因: {result.fallback_reason}")
    print(f"位点区间: {result.from_version} -> {result.to_version}")
    print(f"记录数:   {result.record_count}（增量文件含墓碑）")
    print(f"耗时:     {result.elapsed_seconds * 1000:.2f} ms")
    print(f"文件:     {result.path}")


def cmd_verify(args) -> None:
    report = reconcile.verify(
        full_path=args.base,
        inc_path=args.incremental,
        expected_full_path=args.full,
    )
    print(json.dumps({k: v for k, v in report.items() if k != "problems"},
                     ensure_ascii=False, indent=2))
    if report["problems"]:
        for rid, msg in report["problems"][:20]:
            print(f"  DIFF {rid}: {msg}")
        raise SystemExit(1)
    print("对拍通过：0 处不一致")


def main() -> None:
    parser = argparse.ArgumentParser(description="全量/增量导出工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="初始化数据源")
    p_init.add_argument("--db", required=True)
    p_init.add_argument("--rows", type=int, default=100000)
    p_init.add_argument("--payload-size", type=int, default=48)
    p_init.add_argument("--seed", type=int, default=42)
    p_init.set_defaults(func=cmd_init)

    p_mut = sub.add_parser("mutate", help="随机变更数据（更新/删除/新增）")
    p_mut.add_argument("--db", required=True)
    p_mut.add_argument("--changes", type=int, default=1000)
    p_mut.add_argument("--seed", type=int, default=None)
    p_mut.set_defaults(func=cmd_mutate)

    p_exp = sub.add_parser("export", help="按位点自动全量/增量导出")
    p_exp.add_argument("--db", required=True)
    p_exp.add_argument("--checkpoint", required=True)
    p_exp.add_argument("--out-dir", required=True)
    p_exp.set_defaults(func=cmd_export)

    p_ver = sub.add_parser("verify", help="对拍：基线全量+增量 vs 新全量")
    p_ver.add_argument("--base", required=True, help="基线全量导出文件")
    p_ver.add_argument("--incremental", required=True, help="增量导出文件")
    p_ver.add_argument("--full", default=None, help="变更后的全量导出文件")
    p_ver.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
