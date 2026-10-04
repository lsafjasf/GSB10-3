#!/usr/bin/env python3
"""演示：遍历四个场景的调度空间，并做一次“记录 -> 重放”断言。

用法:
  python3 explore_scenarios.py                 # 打印报告
  python3 explore_scenarios.py --samples DIR   # 同时把样例交错序列写为 JSON
"""

import argparse
import os

import scenarios
from reprosched import (
    RandomPolicy,
    ReplayPolicy,
    explore,
    format_events,
    run_program,
    to_json,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", metavar="DIR", help="把样例交错序列写入该目录")
    args = parser.parse_args()

    print("=" * 78)
    print("1) 调度空间遍历（无状态 DFS，枚举每个调度点的所有可运行线程）")
    print("=" * 78)
    print(f"{'场景':<22}{'遍历调度数':>10}{'失败调度':>10}{'不同缺陷':>10}  说明")
    summary = []
    for factory in scenarios.ALL:
        prog, check, desc = factory()
        stats = explore(prog, check=check)
        summary.append((factory.__name__, stats, desc))
        print(f"{factory.__name__:<22}{stats.schedules:>10}{stats.failing:>10}"
              f"{stats.defects:>10}  {desc}")

    print()
    print("=" * 78)
    print("2) 一个失败调度的完整交错序列（single_race 的丢失更新）")
    print("=" * 78)
    prog, check, _ = scenarios.single_race()
    stats = explore(prog, check=check)
    bad = next(iter(stats.signatures.values()))[1]
    print(f"choices = [{', '.join(bad.trace)}]")
    print(f"final_state = {bad.final_state}, check_error = {bad.check_error}")
    print(format_events(bad))

    print()
    print("=" * 78)
    print("3) 记录 -> 重放断言（同一条交错序列必须复现完全相同的事件与最终状态）")
    print("=" * 78)
    for name in ("no_race", "single_race", "deadlock", "specific_interleaving"):
        # 从随机记录中确定性地挑一条：有缺陷的场景挑命中缺陷的序列
        recorded = None
        for seed in range(1000):
            prog, check, _ = getattr(scenarios, name)()
            candidate = run_program(prog, policy=RandomPolicy(seed), check=check)
            if name == "no_race" or candidate.bug:
                recorded = candidate
                break

        prog2, check2, _ = getattr(scenarios, name)()
        replayed = run_program(prog2, policy=ReplayPolicy(recorded.trace), check=check2)

        assert replayed.trace == recorded.trace, "trace 不一致"
        assert replayed.events == recorded.events, "可观察事件序列不一致"
        assert replayed.final_state == recorded.final_state, "最终状态不一致"
        assert replayed.outcome == recorded.outcome, "结果不一致"
        assert replayed.bug == recorded.bug, "缺陷判定不一致"
        print(f"[PASS] {name:<22} replay({len(replayed.trace)} 步) "
              f"events/final_state/outcome 全部相同, bug={replayed.bug}, "
              f"final={replayed.final_state}")

    if args.samples:
        os.makedirs(args.samples, exist_ok=True)
        print()
        print("=" * 78)
        print(f"4) 写出样例交错序列到 {args.samples}/")
        print("=" * 78)
        for name in ("no_race", "single_race", "deadlock", "specific_interleaving"):
            prog, check, _ = getattr(scenarios, name)()
            stats = explore(prog, check=check)
            if stats.failing:
                result = next(iter(stats.signatures.values()))[1]
            else:
                result = run_program(prog, check=check)
            path = os.path.join(args.samples, f"{name}.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write(to_json(result, scenario=name))
            print(f"  wrote {path} ({'BUG' if result.bug else 'ok'}, {len(result.trace)} 步)")


if __name__ == "__main__":
    main()
