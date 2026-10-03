"""确定性虚拟时钟仿真：对比三种退避策略在不同核数/临界区长度下的尝试次数。

模型：
- N 个“核”各自反复执行 [抢锁 -> 临界区(cs_len) -> 临界区外工作(outside)]；
- 每个核有自己的虚拟时间线 next_time，抢锁失败按策略 delay 推进自己的时钟；
- 锁同一时刻只有一个持有者（仿真按时间最小者逐个裁决，天然保证互斥）；
- 抖动策略注入固定种子的 random，结果完全可复现。

运行：python3 simulate.py [--iterations N] [--csv attempts_data.csv]
"""

from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

from backoff import (
    Action,
    BackoffStrategy,
    ExponentialBackoff,
    ExponentialJitterBackoff,
    FixedBackoff,
)

SPIN_COST = 1e-7   # 一次纯自旋重试的虚拟开销（约一条 CAS 循环）
YIELD_COST = 1e-6  # 一次让出时间片的虚拟开销


@dataclass
class SimResult:
    strategy: str
    cores: int
    cs_len_us: float
    total_attempts: int
    total_failures: int
    virtual_time_us: float

    @property
    def attempts_per_acquire(self) -> float:
        return self.total_attempts / (self.total_attempts - self.total_failures)


def simulate(
    strategy_factory: Callable[[], BackoffStrategy],
    cores: int,
    cs_len: float,
    iterations: int,
    seed: int = 0,
) -> Tuple[int, int, float]:
    """返回 (总尝试次数, 总失败次数, 虚拟耗时秒)。"""
    rng = random.Random(seed)
    outside = cs_len  # 临界区外工作量，与临界区等长，保证锁有持续需求
    next_time = [0.0] * cores
    consec_fails = [0] * cores   # 连续失败次数，喂给策略
    total_fails = [0] * cores    # 累计失败次数，用于统计
    done = [0] * cores
    attempts = [0] * cores
    strategies = [strategy_factory() for _ in range(cores)]
    release_time = 0.0
    held = False

    while min(done) < iterations:
        # 取虚拟时间最早、且还没做完的核来裁决
        core = min(
            (c for c in range(cores) if done[c] < iterations),
            key=lambda c: (next_time[c], c),
        )
        t = next_time[core]
        attempts[core] += 1
        if not held or t >= release_time:
            # 抢锁成功：占用临界区，然后做临界区外工作
            held = True
            release_time = t + cs_len
            next_time[core] = release_time + outside
            done[core] += 1
            consec_fails[core] = 0
            strategies[core].reset()
            if done[core] >= iterations and all(d >= iterations for d in done):
                held = False
        else:
            # 抢锁失败：按策略推进自己的虚拟时钟
            consec_fails[core] += 1
            total_fails[core] += 1
            decision = strategies[core].decide(consec_fails[core])
            if decision.action is Action.SPIN:
                next_time[core] = t + SPIN_COST
            elif decision.action is Action.YIELD:
                next_time[core] = t + YIELD_COST
            else:
                next_time[core] = t + decision.delay
        # 锁自然到期
        if held and min(next_time[c] for c in range(cores) if done[c] < iterations) >= release_time:
            held = False

    return sum(attempts), sum(total_fails), max(next_time)


def make_factories(seed: int) -> Dict[str, Callable[[], BackoffStrategy]]:
    return {
        "fixed": lambda: FixedBackoff(spins=16, then=Action.YIELD),
        "exponential": lambda: ExponentialBackoff(base=1e-6, factor=2.0, cap=1e-3, then=Action.YIELD),
        "exponential_jitter": lambda: ExponentialJitterBackoff(
            base=1e-6, factor=2.0, cap=1e-3, then=Action.YIELD,
            random_fn=random.Random(seed).random,
        ),
    }


def run_grid(iterations: int) -> List[SimResult]:
    results: List[SimResult] = []
    core_grid = [1, 2, 4, 8, 16]
    cs_grid_us = [0.5, 10.0, 200.0]  # 短 / 中 / 长临界区
    for cs_us in cs_grid_us:
        for cores in core_grid:
            for name, factory in make_factories(seed=42).items():
                total, failed, vtime = simulate(factory, cores, cs_us * 1e-6, iterations, seed=42)
                results.append(SimResult(name, cores, cs_us, total, failed, vtime * 1e6))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=200, help="每个核进入临界区的次数")
    parser.add_argument("--csv", default="attempts_data.csv", help="数据输出路径")
    args = parser.parse_args()

    results = run_grid(args.iterations)

    with open(args.csv, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["strategy", "cores", "cs_len_us", "total_attempts",
                         "total_failures", "attempts_per_acquire", "virtual_time_us"])
        for r in results:
            writer.writerow([r.strategy, r.cores, r.cs_len_us, r.total_attempts,
                             r.total_failures, f"{r.attempts_per_acquire:.2f}",
                             f"{r.virtual_time_us:.1f}"])

    # 终端表格：按 (临界区, 核数) 分组，横向对比三种策略
    by_key: Dict[Tuple[float, int], Dict[str, SimResult]] = {}
    for r in results:
        by_key.setdefault((r.cs_len_us, r.cores), {})[r.strategy] = r

    print(f"iterations/core = {args.iterations}")
    header = f"{'cs_len':>8} {'cores':>5} | {'fixed':>12} {'exponential':>12} {'exp_jitter':>12} | {'exp_jitter time(us)':>20}"
    print(header)
    print("-" * len(header))
    for (cs_us, cores) in sorted(by_key):
        row = by_key[(cs_us, cores)]
        print(f"{cs_us:>8.1f} {cores:>5d} | "
              f"{row['fixed'].total_attempts:>12d} "
              f"{row['exponential'].total_attempts:>12d} "
              f"{row['exponential_jitter'].total_attempts:>12d} | "
              f"{row['exponential_jitter'].virtual_time_us:>20.1f}")
    print(f"\n数据已写入 {args.csv}")


if __name__ == "__main__":
    main()
