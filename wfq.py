"""带权重的公平调度器（纯标准库）。

算法：平滑加权轮询（Smooth Weighted Round Robin，nginx 同款思想）。

对每个类别维护 current_weight，每次选择时：
    1. 对所有"有积压且权重 > 0"的类别：current_weight += weight
    2. 选择 current_weight 最大的类别处理一个请求
    3. 被选中类别：current_weight -= 所有正权重之和 total_weight

数学性质（在参与类别集合不变时）：
    - 任意连续 total_weight 次选择中，类别 i 恰好被选中 weight_i 次
    - 类别 i 两次被选中之间的最大间隔不超过 total_weight / weight_i 次
    => 任何正权重类别都不会饥饿，且处理机会严格按权重比例分配。

权重为 0 的类别语义（明确定义）：
    - 只要存在"有积压且权重 > 0"的类别，0 权重类别永远不被选中；
    - 当所有有积压的类别权重都为 0 时，按类别注册顺序做轮询
      （round-robin），保证系统不死锁且各类别机会均等；
    - 所有类别都无积压时，next_request() 返回 None。

确定性：current_weight 相同时，按类别注册顺序（最早注册优先）打破平局，
因此相同的输入序列必定产生相同的输出序列。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class _ClassState:
    name: str
    weight: int
    current_weight: int = 0
    queue: deque = field(default_factory=deque)


class WeightedFairScheduler:
    """按权重公平分配处理机会、对所有正权重类别无饥饿的调度器。"""

    def __init__(self) -> None:
        self._states: dict[str, _ClassState] = {}
        self._order: list[str] = []  # 注册顺序，同时用于确定性平局判定
        self._zero_cursor = 0        # 全零权重轮询游标（确定性）

    def add_class(self, name: str, weight: int) -> None:
        """注册一个调度类别。weight 必须是非负整数；重复注册抛 ValueError。"""
        if not isinstance(weight, int) or isinstance(weight, bool):
            raise TypeError(f"weight 必须是 int，得到 {type(weight).__name__}")
        if weight < 0:
            raise ValueError(f"weight 必须 >= 0，得到 {weight}")
        if name in self._states:
            raise ValueError(f"类别 {name!r} 已存在")
        self._states[name] = _ClassState(name=name, weight=weight)
        self._order.append(name)

    def submit(self, name: str, item: Any) -> None:
        """向某个类别提交一个待处理请求（按 FIFO 排队）。"""
        try:
            state = self._states[name]
        except KeyError:
            raise KeyError(f"类别 {name!r} 未注册") from None
        state.queue.append(item)

    def pending(self, name: Optional[str] = None) -> int:
        """返回某类别（或全部类别）的积压请求数。"""
        if name is not None:
            return len(self._states[name].queue)
        return sum(len(s.queue) for s in self._states.values())

    def _select(self) -> Optional[_ClassState]:
        eligible = [self._states[n] for n in self._order if self._states[n].queue]
        if not eligible:
            return None
        positive = [s for s in eligible if s.weight > 0]
        if not positive:
            # 所有有积压类别权重均为 0：按注册顺序轮询，避免死锁
            n = len(self._order)
            for step in range(n):
                name = self._order[(self._zero_cursor + step) % n]
                state = self._states[name]
                if state.queue:
                    self._zero_cursor = (self._zero_cursor + step + 1) % n
                    return state
            return None  # 理论上不可达：eligible 非空

        total_weight = sum(s.weight for s in positive)
        for state in positive:
            state.current_weight += state.weight
        # 平局时取注册顺序最靠前的类别（_order 中的最小下标）
        best = positive[0]
        best_index = self._order.index(best.name)
        for state in positive[1:]:
            index = self._order.index(state.name)
            if state.current_weight > best.current_weight or (
                state.current_weight == best.current_weight and index < best_index
            ):
                best, best_index = state, index
        best.current_weight -= total_weight
        return best

    def next_request(self) -> Optional[tuple[str, Any]]:
        """取下一个应处理的请求，返回 (类别名, 请求)；无积压时返回 None。"""
        state = self._select()
        if state is None:
            return None
        return state.name, state.queue.popleft()


def _fairness_demo() -> None:
    """输出公平性数据：比例准确度 + 高压下低优先级不饥饿。"""
    print("=" * 64)
    print("场景 A：三类权重 5:3:2，共处理 10000 次（全部持续有积压）")
    print("=" * 64)
    sched = WeightedFairScheduler()
    weights = {"high": 5, "mid": 3, "low": 2}
    for name, w in weights.items():
        sched.add_class(name, w)
        for i in range(10000):
            sched.submit(name, i)

    n = 10000
    counts = {name: 0 for name in weights}
    for _ in range(n):
        name, _ = sched.next_request()
        counts[name] += 1
    total_weight = sum(weights.values())
    print(f"{'类别':<6}{'权重':>6}{'期望次数':>10}{'实际次数':>10}{'偏差':>8}")
    for name, w in weights.items():
        expected = n * w / total_weight
        print(f"{name:<6}{w:>6}{expected:>10.1f}{counts[name]:>10d}"
              f"{counts[name] - expected:>+8.1f}")

    print()
    print("=" * 64)
    print("场景 B：持续高压 high:low = 1000:1，模拟 100000 个时刻")
    print("  每个时刻都有新的 high 请求到达；low 请求每隔 2000 时刻来一个")
    print("  （low 到达速率低于其 1/1001 的服务份额，队列不会无限积压）")
    print("=" * 64)
    sched = WeightedFairScheduler()
    sched.add_class("high", 1000)
    sched.add_class("low", 1)

    ticks = 100000
    counts = {"high": 0, "low": 0}
    waits: list[int] = []
    pending_low_at: dict[int, int] = {}  # low 请求 id -> 到达时刻
    low_seq = 0
    for tick in range(ticks):
        sched.submit("high", tick)  # 高压：高优先级请求源源不断
        if tick % 2000 == 0:
            sched.submit("low", low_seq)
            pending_low_at[low_seq] = tick
            low_seq += 1
        name, item = sched.next_request()
        counts[name] += 1
        if name == "low":
            waits.append(tick - pending_low_at.pop(item))

    print(f"{'类别':<6}{'权重':>6}{'处理次数':>10}")
    print(f"{'high':<6}{1000:>6}{counts['high']:>10d}")
    print(f"{'low':<6}{1:>6}{counts['low']:>10d}")
    print(f"low 请求到达总数: {low_seq}，已处理: {len(waits)}，"
          f"未处理积压: {sched.pending('low')}")
    print(f"low 请求等待时刻：最大 {max(waits)}，平均 {sum(waits) / len(waits):.1f}")
    print(f"理论上界 total_weight/weight_low = 1001，"
          f"实测最大间隔上界 {max(waits)} -> 低优先级未饥饿")


if __name__ == "__main__":
    _fairness_demo()
