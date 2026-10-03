"""匹配耗时基准：Trie vs 朴素全量扫描。

直接运行：``python3 benchmark.py``。仅依赖标准库。
"""

from __future__ import annotations

import random
import statistics
import time

from topic_broker import (
    MULTI_LEVEL_WILDCARD,
    SINGLE_LEVEL_WILDCARD,
    SubscriptionTree,
    _Node,
    filter_matches,
)

DEPTH = 5
VOCAB = [f"tok{i:02d}" for i in range(12)]
SIZES = [1_000, 10_000, 50_000, 100_000]
SAMPLE_TOPICS = 200


def make_filter(rng: random.Random) -> str:
    levels = []
    for _ in range(DEPTH):
        r = rng.random()
        if r < 0.1:
            levels.append(SINGLE_LEVEL_WILDCARD)
        else:
            levels.append(rng.choice(VOCAB))
    if rng.random() < 0.15:
        levels.append(MULTI_LEVEL_WILDCARD)
    return "/".join(levels)


def make_topic(rng: random.Random) -> str:
    return "/".join(rng.choice(VOCAB) for _ in range(DEPTH))


def build_dataset(n: int, seed: int = 42):
    rng = random.Random(seed)
    tree = SubscriptionTree()
    filters = set()
    while len(filters) < n:
        filters.add(make_filter(rng))
    filters = list(filters)
    for i, f in enumerate(filters):
        tree.subscribe(f"sub-{i:07d}", f)
    topic_rng = random.Random(seed + 1)
    topics = [make_topic(topic_rng) for _ in range(SAMPLE_TOPICS)]
    return tree, filters, topics


class CountingTree(SubscriptionTree):
    """统计一次匹配访问的 Trie 节点数（证明不按订阅数遍历）。"""

    def __init__(self) -> None:
        super().__init__()
        self.visited = 0

    def _collect(self, node: _Node, levels, index, out) -> None:
        self.visited += 1
        super()._collect(node, levels, index, out)


def time_trie(tree, topics, repeats):
    timings = []
    for _ in range(repeats):
        start = time.perf_counter()
        total_hits = 0
        for topic in topics:
            total_hits += len(tree.subscribers_for(topic))
        timings.append((time.perf_counter() - start) / len(topics))
    return timings, total_hits


def time_naive(filters, topics, max_budget_s: float = 3.0):
    """全量扫描：每个主题逐一对所有订阅做 filter_matches。"""
    timings = []
    deadline = time.monotonic() + max_budget_s
    rounds = 0
    while True:
        start = time.perf_counter()
        total_hits = 0
        for topic in topics:
            for f in filters:
                if filter_matches(f, topic):
                    total_hits += 1
        timings.append((time.perf_counter() - start) / len(topics))
        rounds += 1
        if time.monotonic() >= deadline or rounds >= 5:
            break
    return timings, total_hits


def us(seconds: float) -> float:
    return seconds * 1_000_000


def main() -> None:
    print(f"过滤器层级深度 L = {DEPTH}（约 15% 带 '#'），每点采样 "
          f"{SAMPLE_TOPICS} 个主题")
    header = (
        f"{'订阅数 N':>9} | {'Trie 中位(us)':>13} {'最小(us)':>10} | "
        f"{'全扫中位(us)':>13} | {'加速比':>8} | {'Trie 访问节点':>12}"
    )
    print(header)
    print("-" * len(header))
    for n in SIZES:
        tree, filters, topics = build_dataset(n)
        counting = CountingTree()
        for i, f in enumerate(filters):
            counting.subscribe(f"sub-{i:07d}", f)
        visit_counts = []
        for topic in topics:
            counting.visited = 0
            counting.subscribers_for(topic)
            visit_counts.append(counting.visited)

        repeats = max(3, min(30, 300_000 // n))
        trie_timings, trie_hits = time_trie(tree, topics, repeats)
        naive_timings, naive_hits = time_naive(filters, topics)
        assert trie_hits == naive_hits, "对拍不一致！"
        speedup = statistics.median(naive_timings) / statistics.median(
            trie_timings
        )
        print(
            f"{n:>9,} | {us(statistics.median(trie_timings)):>13.2f} "
            f"{us(min(trie_timings)):>10.2f} | "
            f"{us(statistics.median(naive_timings)):>13.0f} | "
            f"{speedup:>7.0f}x | {max(visit_counts):>12}"
        )
    print()
    print("说明：Trie 单次匹配只沿层级向下，每层最多展开 精确/+/  # 三条边；")
    print("      访问节点数只取决于层级深度 L（本表最坏访问数），与 N 无关。")
    print()
    exact_table()


def exact_table() -> None:
    """对照：全部为精确过滤器（无通配符），单次匹配恒走 L 个节点。"""
    rng = random.Random(123)
    print("纯精确订阅（无通配符，L = 5），隔离 N 本身的影响：")
    header = (
        f"{'订阅数 N':>9} | {'Trie 中位(us)':>13} {'最小(us)':>10} | "
        f"{'Trie 访问节点':>12} | {'每次命中订阅数':>14}"
    )
    print(header)
    print("-" * len(header))
    for n in SIZES:
        tree = SubscriptionTree()
        used = set()
        while len(used) < n:
            f = "/".join(rng.choice(VOCAB) for _ in range(DEPTH))
            used.add(f)
        for i, f in enumerate(used):
            tree.subscribe(f"sub-{i:07d}", f)
        topics = [make_topic(rng) for _ in range(SAMPLE_TOPICS)]

        counting = CountingTree()
        for i, f in enumerate(used):
            counting.subscribe(f"sub-{i:07d}", f)
        visit_counts = []
        hit_counts = []
        for topic in topics:
            counting.visited = 0
            hits = counting.subscribers_for(topic)
            visit_counts.append(counting.visited)
            hit_counts.append(len(hits))

        timings, _ = time_trie(tree, topics, repeats=20)
        print(
            f"{n:>9,} | {us(statistics.median(timings)):>13.2f} "
            f"{us(min(timings)):>10.2f} | {max(visit_counts):>12} | "
            f"{max(hit_counts):>14}"
        )
    print("      访问节点恒为 L+1，N 从 1k 增到 100k 单次匹配耗时基本不变。")


if __name__ == "__main__":
    main()
