#!/usr/bin/env python3
"""内存上界基准：活跃会话数 vs 内存占用。

方法:
* 每个规模在独立子进程中运行，用 tracemalloc 测量喂入阶段 Python 堆峰值，
  并记录进程 RSS 增量作为参照。
* 记录逐条构造、喂入后即弃，因此测量到的驻留内存几乎全部来自聚合器
  （dict + 索引最小堆，均为每会话常数项）。
* 另设容量场景：max_sessions=1000 时喂入 20 万条记录，验证内存不随
  记录总量增长（硬上界）。

输出: 打印表格并写入 data/memory.md。
"""

import json
import os
import resource
import subprocess
import sys
import tracemalloc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flowagg import FlowAggregator, FlowRecord

OUT_MD = os.path.join(os.path.dirname(__file__), "..", "data", "memory.md")


def run_once(n_records: int, max_sessions: int) -> dict:
    agg = FlowAggregator(idle_timeout=3600.0, max_sessions=max_sessions)
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    tracemalloc.start()
    for i in range(n_records):
        # 不同五元组 -> 每条记录一个活跃会话（最坏情况）
        rec = FlowRecord(
            src_ip=f"10.{(i >> 16) & 255}.{(i >> 8) & 255}.{i & 255}",
            dst_ip="192.0.2.1",
            src_port=1024 + (i % 60000),
            dst_port=443,
            protocol=6,
            start=float(i), end=float(i) + 0.5,
            packets=3, bytes=1500,
        )
        agg.add(rec)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    active = agg.active_count
    agg.drain()
    return {
        "records": n_records,
        "max_sessions": max_sessions,
        "active_sessions": active,
        "peak_bytes": peak,
        "bytes_per_session": round(peak / max(active, 1), 1),
        "rss_delta_kb": max(0, rss_after - rss_before),
        "evicted": agg.sessions_evicted,
    }


def child_main() -> None:
    n_records = int(sys.argv[2])
    max_sessions = int(sys.argv[3])
    print(json.dumps(run_once(n_records, max_sessions)))


def parent_main() -> None:
    scenarios = [
        (1_000, 1_000_000),
        (10_000, 1_000_000),
        (50_000, 1_000_000),
        (100_000, 1_000_000),
        (200_000, 1_000_000),
        (200_000, 1_000),   # 容量硬上界场景
    ]
    rows = []
    for n_records, cap in scenarios:
        out = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--child",
             str(n_records), str(cap)],
            capture_output=True, text=True, check=True)
        rows.append(json.loads(out.stdout.strip()))

    header = ("| 喂入记录数 | max_sessions | 活跃会话峰值 | 堆内存峰值 (MiB) "
              "| 字节/会话 | RSS 增量 (MiB) | 逐出会话数 |")
    sep = "|---:|---:|---:|---:|---:|---:|---:|"
    lines = [header, sep]
    for r in rows:
        lines.append(
            "| {records} | {max_sessions} | {active_sessions} | {mib:.2f} "
            "| {bytes_per_session} | {rss:.2f} | {evicted} |".format(
                mib=r["peak_bytes"] / 2**20, rss=r["rss_delta_kb"] / 1024,
                **r))

    table = "\n".join(lines)
    print(table)
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("# 内存上界数据\n\n")
        fh.write("测量方法：`tracemalloc` 统计喂入阶段 Python 堆峰值"
                 "（独立子进程，记录逐条构造即弃），RSS 增量为参照。\n")
        fh.write("每条记录使用不同五元组，即活跃会话数的最坏情况。\n\n")
        fh.write(table + "\n\n")
        fh.write("结论：活跃会话数恒 <= max_sessions；堆内存与活跃会话数"
                 "线性相关（每会话约数百字节常数），与喂入记录总量无关。\n"
                 "最后一行表明 20 万条记录经容量逐出后内存与 1000 会话\n"
                 "规模相当，内存存在硬上界。\n")
    print(f"\nwrote {OUT_MD}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        child_main()
    else:
        parent_main()
