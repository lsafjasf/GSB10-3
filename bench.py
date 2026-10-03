"""基准：订阅规模 vs 单次匹配耗时（Trie vs 朴素全量遍历）。

用法:
    python3 bench.py                 # 运行完整基准并打印表格
    python3 bench.py --out results/bench.tsv   # 同时落盘 TSV
"""

from __future__ import annotations

import argparse
import random
import time

from subtree import SubscriptionTree

VOCAB = ["sensor", "alarm", "room", "zone", "sys", "net", "db", "web"]
LEVELS = 4


def make_filter(rng: random.Random) -> str:
    parts: list[str] = []
    for i in range(rng.randint(2, LEVELS)):
        roll = rng.random()
        if roll < 0.15:
            parts.append("+")
        elif roll < 0.25 and i >= 2:
            parts.append("#")
            return "/".join(parts)
        else:
            parts.append(rng.choice(VOCAB) + str(rng.randrange(8)))
    return "/".join(parts)


def make_topic(rng: random.Random) -> str:
    depth = rng.randint(2, LEVELS)
    return "/".join(rng.choice(VOCAB) + str(rng.randrange(8)) for _ in range(depth))


def filter_matches(topic_filter: str, levels: list[str]) -> bool:
    parts = topic_filter.split("/")
    for i, part in enumerate(parts):
        if part == "#":  # '#' 只出现在末尾，此前各层均已匹配
            return True
        if i >= len(levels) or (part != "+" and part != levels[i]):
            return False
    return len(parts) == len(levels)


def linear_match(filters: list[str], topic: str) -> list[str]:
    """朴素实现：对全部订阅逐一做通配匹配。"""
    levels = topic.split("/")
    return [f for f in filters if filter_matches(f, levels)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="")
    parser.add_argument("--sizes", default="100,1000,5000,20000,100000")
    args = parser.parse_args()

    rng = random.Random(20261003)
    all_filters: list[str] = []
    seen: set[str] = set()
    while len(all_filters) < 100_000:
        topic_filter = make_filter(rng)
        if topic_filter not in seen:
            seen.add(topic_filter)
            all_filters.append(topic_filter)

    queries = [make_topic(rng) for _ in range(200)]
    sizes = [int(s) for s in args.sizes.split(",")]

    rows: list[str] = [
        "subscriptions\tmatch_us_avg\tmatch_us_p99\tvisited_nodes\tlinear_us_avg"
    ]
    print(f"{'订阅数':>10} | {'Trie 平均μs':>12} | {'p99 μs':>8} | "
          f"{'访问节点':>8} | {'全量扫描平均μs':>14}")
    print("-" * 68)

    for size in sizes:
        tree = SubscriptionTree()
        for i, topic_filter in enumerate(all_filters[:size]):
            tree.subscribe(topic_filter, i)

        # Trie：每个查询重复足够多次以获得稳定计时。
        repeats = max(1, 200_000 // max(size, 1) if size <= 5_000 else
                      (2000 if size <= 20_000 else 400))
        timings: list[float] = []
        visited_total = 0
        begin = time.perf_counter()
        for _ in range(repeats):
            for topic in queries:
                t0 = time.perf_counter()
                _, visited = tree.match_with_stats(topic)
                timings.append((time.perf_counter() - t0) * 1e6)
                visited_total += visited
        elapsed = time.perf_counter() - begin
        timings.sort()
        avg_us = sum(timings) / len(timings)
        p99_us = timings[int(len(timings) * 0.99)]
        visited_avg = visited_total / len(timings)

        # 朴素全量扫描：只在小规模跑，避免大 N 时耗时过长。
        if size <= 5_000:
            linear_repeats = max(1, 2_000 // size)
            lbegin = time.perf_counter()
            for _ in range(linear_repeats):
                for topic in queries:
                    linear_match(all_filters[:size], topic)
            linear_us = (time.perf_counter() - lbegin) * 1e6 / (linear_repeats * len(queries))
            linear_cell = f"{linear_us:14.1f}"
        else:
            linear_cell = f"{'O(N) 未测':>14}"

        print(f"{size:>10} | {avg_us:>12.2f} | {p99_us:>8.2f} | "
              f"{visited_avg:>8.1f} | {linear_cell}")
        rows.append(
            f"{size}\t{avg_us:.3f}\t{p99_us:.3f}\t{visited_avg:.1f}\t"
            + (f"{linear_us:.3f}" if size <= 5_000 else "")
        )

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(rows) + "\n")
        print(f"\n数据已写入 {args.out}")


if __name__ == "__main__":
    main()
