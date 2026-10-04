#!/usr/bin/env python3
"""分层采样演示：生成模拟日志流，跑四个场景，输出实际/目标采样率对照。

用法：
    python3 demo.py                 # 运行全部场景，打印报告并写入 reports/
    python3 demo.py --out reports   # 指定报告目录

场景：
    keep_all   全保留（default_rate=1.0）
    drop_all   全丢弃（default_rate=0.0，但错误/关键路径仍强制保留）
    mixed      分级分通道混合采样率
    update     混合规则运行一半后原子更新为新规则（分段对照）
"""

from __future__ import annotations

import argparse
import os
import random
from typing import Dict, Iterable, List, Tuple

from sampling import (
    BufferedRequestSampler,
    Event,
    LayeredSampler,
    Level,
    Metrics,
    Rule,
    assert_request_consistency,
)

CHANNELS = ["http", "db", "billing", "auth", "worker"]
# 普通级别 -> 每请求平均条数
LEVEL_MEAN_COUNT: Dict[Level, float] = {
    Level.DEBUG: 6.0,
    Level.INFO: 3.0,
    Level.WARN: 1.0,
}
# 错误级别 -> 每请求出现概率（出现即 1 条，用于演示强制保留）
ERROR_PROB = {Level.ERROR: 0.06, Level.FATAL: 0.01}


def generate_requests(
    n_requests: int, seed: int
) -> List[List[Event]]:
    """生成 n 个请求的事件流（确定性，seed 可复现）。"""
    rng = random.Random(seed)
    requests: List[List[Event]] = []
    for i in range(n_requests):
        rid = f"req-{i:06d}"
        channel = rng.choice(CHANNELS)
        # 少量请求走关键路径
        path = "/api/critical/checkout" if rng.random() < 0.03 else "/api/normal"
        events: List[Event] = []

        def add(level: Level, count: int) -> None:
            for _ in range(count):
                events.append(
                    Event(
                        request_id=rid,
                        level=level,
                        channel=channel,
                        message=f"{level.name} on {channel}",
                        path=path,
                    )
                )

        for level, mean in LEVEL_MEAN_COUNT.items():
            # 简化的泊松：以 mean 为期望的非负整数
            add(level, max(0, int(rng.expovariate(1.0 / mean) + 0.5)))
        for level, prob in ERROR_PROB.items():
            if rng.random() < prob:
                add(level, 1)
        if not events:  # 保证每请求至少一条
            add(Level.INFO, 1)
        requests.append(events)
    return requests


def run_requests(
    sampler: LayeredSampler,
    requests: Iterable[List[Event]],
    metrics: Metrics,
) -> List[Tuple[float, bool]]:
    """用 BufferedRequestSampler 跑一批请求并统计，附带一致性断言。

    返回 (治理目标率, 是否保留) 列表，用于请求级决策对照。
    """
    rs = BufferedRequestSampler(sampler)
    decisions: List[Tuple[float, bool]] = []
    for events in requests:
        for ev in events:
            rs.record(ev)
        result = rs.end_request(events[0].request_id)
        kept_ids = {e.request_id for e in result.events}
        # 一致性断言：该请求要么全留要么全丢
        assert_request_consistency(
            result.request_id,
            [e.request_id in kept_ids for e in events],
        )
        decisions.append((result.match.rate, result.kept))
        for ev in events:
            # 目标率用请求级治理率（含错误/关键路径强制保留的耦合效应），
            # 这样“实际 vs 目标”才是同口径对照。
            metrics.record(
                ev,
                ev.request_id in kept_ids,
                result.match.rate,
                _source(result.match),
            )
    return decisions


def decision_table(decisions: List[Tuple[float, bool]]) -> str:
    """请求级决策对照：同一治理目标率下，请求实际保留率应≈目标率。"""
    buckets: Dict[float, List[int]] = {}
    for rate, kept in decisions:
        b = buckets.setdefault(rate, [0, 0])
        b[0] += 1
        b[1] += 1 if kept else 0
    lines = [
        "请求级决策对照（同一请求全留/全丢，治理目标率 -> 实际保留率）：",
        "",
        "| 治理目标率 | 请求数 | 保留请求数 | 实际保留率 | 绝对偏差 |",
        "|---:|---:|---:|---:|---:|",
    ]
    for rate in sorted(buckets):
        n, k = buckets[rate]
        actual = k / n
        lines.append(f"| {rate:.2f} | {n} | {k} | {actual:.4f} | {abs(actual - rate):.4f} |")
    return "\n".join(lines) + "\n"


def _source(m) -> str:
    if m.priority == 0:
        return "forced(错误/关键路径)"
    if m.rule is not None and m.rule.name:
        return f"rule:{m.rule.name}"
    return "default"


def mixed_rules() -> List[Rule]:
    return [
        Rule(Level.DEBUG, rate=0.05, name="debug-5pct"),
        Rule(Level.INFO, rate=0.30, name="info-30pct"),
        Rule(Level.WARN, rate=0.80, name="warn-80pct"),
        Rule(Level.INFO, "billing", rate=0.90, name="info-billing-90pct"),
        Rule(None, "auth", keep=True, name="auth-keep"),
    ]


def updated_rules() -> List[Rule]:
    return [
        Rule(Level.DEBUG, rate=0.20, name="debug-20pct"),
        Rule(Level.INFO, rate=0.60, name="info-60pct"),
        Rule(Level.WARN, rate=0.50, name="warn-50pct"),
        Rule(Level.INFO, "billing", rate=0.40, name="info-billing-40pct"),
        Rule(None, "auth", keep=True, name="auth-keep"),
    ]


def make_sampler(rules, default_rate) -> LayeredSampler:
    return LayeredSampler(
        rules=rules,
        default_rate=default_rate,
        protected_channels={"billing-critical"},
        protected_path_prefixes=("/api/critical",),
    )


def scenario(name: str, sampler: LayeredSampler, requests):
    metrics = Metrics()
    decisions = run_requests(sampler, requests, metrics)
    return metrics, decisions


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports")
    ap.add_argument("--requests", type=int, default=8000)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    requests = generate_requests(args.requests, seed=42)
    half = len(requests) // 2

    md_parts: List[str] = ["# 分层采样：实际 vs 目标采样率对照", ""]
    md_parts.append(
        f"工作负载: {args.requests} 个请求 / "
        f"{sum(len(r) for r in requests)} 条事件, seed=42 可复现。"
    )
    md_parts.append("")
    md_parts.append(
        "> 口径说明：决策在请求级做出（同一请求全留/全丢），因此精度判据看"
        "“请求级决策对照”表；事件级表中的 95%CI 按独立同分布近似计算，"
        "同请求内事件共享一次决策存在相关性，个别行略出区间属预期现象。"
        "事件级目标率为该组事件的平均治理目标率（含错误/关键路径强制保留"
        "带来的 1.0 混合）。"
    )
    md_parts.append("")

    # 场景 1：全保留
    m, dec = scenario("keep_all", make_sampler([], 1.0), requests)
    md_parts.append(m.to_markdown("场景 keep_all：全保留 (default_rate=1.0)"))
    md_parts.append(decision_table(dec))
    _write(m, args.out, "keep_all")

    # 场景 2：全丢弃（错误/关键路径仍强制保留）
    m, dec = scenario("drop_all", make_sampler([], 0.0), requests)
    md_parts.append(
        m.to_markdown(
            "场景 drop_all：全丢弃 (default_rate=0.0) —— ERROR/FATAL 与 "
            "关键路径(/api/critical)仍 100% 保留"
        )
    )
    md_parts.append(decision_table(dec))
    _write(m, args.out, "drop_all")

    # 场景 3：混合规则
    m, dec = scenario("mixed", make_sampler(mixed_rules(), 1.0), requests)
    md_parts.append(m.to_markdown("场景 mixed：分级分通道混合采样率"))
    md_parts.append(decision_table(dec))
    _write(m, args.out, "mixed")

    # 场景 4：规则运行中被更新（分段统计）
    sampler = make_sampler(mixed_rules(), 1.0)
    m1, dec1 = scenario("update_p1", sampler, requests[:half])
    sampler.update_rules(updated_rules())
    m2, dec2 = scenario("update_p2", sampler, requests[half:])
    md_parts.append(
        m1.to_markdown("场景 update：更新前 (规则版本 v0)")
    )
    md_parts.append(decision_table(dec1))
    _write(m1, args.out, "update_phase1_v0")
    md_parts.append(
        m2.to_markdown("场景 update：更新后 (规则版本 v1，原子替换)")
    )
    md_parts.append(decision_table(dec2))
    _write(m2, args.out, "update_phase2_v1")

    report_md = os.path.join(args.out, "report.md")
    with open(report_md, "w", encoding="utf-8") as f:
        f.write("\n".join(md_parts) + "\n")

    print("\n".join(md_parts))
    print(f"\n报告已写入 {args.out}/ (report.md + 各场景 CSV)")


def _write(metrics: Metrics, out_dir: str, name: str) -> None:
    path = os.path.join(out_dir, f"{name}.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(metrics.to_csv())


if __name__ == "__main__":
    main()
