"""自适应批大小控制器（仅标准库，时间可注入）。

控制策略：AIMD（Additive Increase / Multiplicative Decrease）
  - 处理耗时高于目标上界，或失败率超阈值：批次快速乘性收小（默认减半）
  - 处理耗时低于目标下界且失败率很低：批次缓慢加性增大（默认 +10%）
  - 落在死区内：保持不变，避免在平衡点附近来回震荡
  - 收小后进入若干轮冷却期，冷却期内禁止增大，防止立刻反弹
  - 批次大小始终被夹紧在固定上下界 [min_size, max_size] 内
"""
from __future__ import annotations

import math
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, List, Optional


@dataclass
class Adjustment:
    """单轮调整记录。"""
    round_index: int
    old_size: int
    new_size: int
    duration: float          # 本轮处理耗时（秒，由调用方或注入时钟给出）
    failure_rate: float      # 本轮失败率 [0, 1]
    reason: str              # latency_high / failure / latency_low / hold / cooldown
    direction: int           # +1 增大, -1 收小, 0 不变


class AdaptiveBatchSizer:
    def __init__(
        self,
        *,
        min_size: int = 10,
        max_size: int = 500,
        initial_size: Optional[int] = None,
        target_latency: float = 0.5,
        deadband: float = 0.10,
        failure_threshold: float = 0.20,
        decrease_factor: float = 0.5,
        increase_ratio: float = 0.10,
        cooldown_rounds: int = 2,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if min_size < 1:
            raise ValueError("min_size 必须 >= 1")
        if max_size < min_size:
            raise ValueError("max_size 必须 >= min_size")
        if target_latency <= 0:
            raise ValueError("target_latency 必须 > 0")
        if not 0 <= deadband < 1:
            raise ValueError("deadband 必须在 [0, 1) 内")
        if not 0 < decrease_factor < 1:
            raise ValueError("decrease_factor 必须在 (0, 1) 内")
        if increase_ratio <= 0:
            raise ValueError("increase_ratio 必须 > 0")
        if not 0 <= failure_threshold <= 1:
            raise ValueError("failure_threshold 必须在 [0, 1] 内")
        if cooldown_rounds < 0:
            raise ValueError("cooldown_rounds 必须 >= 0")

        self.min_size = min_size
        self.max_size = max_size
        self.target_latency = target_latency
        self.deadband = deadband
        self.failure_threshold = failure_threshold
        self.decrease_factor = decrease_factor
        self.increase_ratio = increase_ratio
        self.cooldown_rounds = cooldown_rounds
        self._time_fn = time_fn

        self._size = (
            min(max(initial_size, min_size), max_size)
            if initial_size is not None
            else min_size
        )
        self._cooldown = 0
        self._round = 0
        self.history: List[Adjustment] = []

    @property
    def size(self) -> int:
        """当前（下一批将使用的）批大小。"""
        return self._size

    def _clamp(self, value: int) -> int:
        return max(self.min_size, min(self.max_size, value))

    def record(self, duration: float, failures: int, batch_size: Optional[int] = None) -> int:
        """上报一批的处理结果，返回调整后的批大小。

        duration:    该批实际处理耗时（秒）。
        failures:    该批中失败的消息数。
        batch_size:  该批实际使用的批大小；默认为当前 size（即按建议批次消费）。
        """
        if duration < 0:
            raise ValueError("duration 不能为负")
        if failures < 0:
            raise ValueError("failures 不能为负")

        old = self._size
        used = old if batch_size is None else batch_size
        if used < 1:
            raise ValueError("batch_size 必须 >= 1")
        if failures > used:
            raise ValueError("failures 不能大于 batch_size")

        failure_rate = failures / used
        upper = self.target_latency * (1.0 + self.deadband)
        lower = self.target_latency * (1.0 - self.deadband)

        if failure_rate > self.failure_threshold:
            # 失败率超阈值：乘性收小，最激进地保系统
            raw = int(math.floor(old * self.decrease_factor))
            new, reason = max(self.min_size, raw), "failure"
            self._cooldown = self.cooldown_rounds
        elif duration > upper:
            # 突然变慢：乘性收小（默认减半），并进入冷却期
            raw = int(math.floor(old * self.decrease_factor))
            new, reason = max(self.min_size, raw), "latency_high"
            self._cooldown = self.cooldown_rounds
        elif self._cooldown > 0:
            # 收小后的冷却期：按兵不动，确认效果后再决定，防止反弹震荡
            self._cooldown -= 1
            new, reason = old, "cooldown"
        elif duration < lower and failure_rate <= self.failure_threshold / 2.0:
            # 明显有余量：加性缓慢增大
            raw = old + max(1, int(math.floor(old * self.increase_ratio)))
            new, reason = min(self.max_size, raw), "latency_low"
        else:
            new, reason = old, "hold"

        new = self._clamp(new)
        # 夹紧后未发生实际变化时按 hold 记录，保证“连续同向调整”统计真实
        if new == old and reason in ("latency_high", "failure", "latency_low"):
            reason = "hold"
        direction = (new > old) - (new < old)

        self.history.append(Adjustment(
            round_index=self._round,
            old_size=old,
            new_size=new,
            duration=duration,
            failure_rate=failure_rate,
            reason=reason,
            direction=direction,
        ))
        self._round += 1
        self._size = new
        return new

    def begin(self) -> float:
        """开始一批，返回由注入时钟产生的起始时间戳。"""
        return self._time_fn()

    def end(self, started_at: float, failures: int = 0, batch_size: Optional[int] = None) -> int:
        """结束一批，用注入时钟计算耗时并上报。"""
        duration = self._time_fn() - started_at
        return self.record(duration, failures, batch_size)

    @contextmanager
    def measure(self, failures_holder: Optional[dict] = None, batch_size: Optional[int] = None):
        """便捷用法：

            with sizer.measure() as report:
                ... 处理消息 ...
                report["failures"] = 失败数
        """
        started = self.begin()
        report = {"failures": 0}
        try:
            yield report
        finally:
            if failures_holder is not None:
                failures_holder.update(report)
            self.end(started, report["failures"], batch_size)

    # ---- 稳定性指标 -----------------------------------------------------

    def stability(self, window: int = 50) -> dict:
        """统计最近 window 轮的稳定性数据。

        返回：
          rounds               统计窗口轮数
          mean_size / stddev   批大小均值/标准差（稳态偏差）
          reversals            相邻轮方向反转的次数（震荡次数）
          max_consecutive      最长连续同向调整次数
          hold_ratio           保持不动的比例（越高越稳）
          mean_duration        平均处理耗时
          latency_violations   耗时超出死区上界的轮数
        """
        h = self.history[-window:] if window > 0 else self.history
        n = len(h)
        if n == 0:
            return {"rounds": 0}
        sizes = [a.new_size for a in h]
        mean = sum(sizes) / n
        var = sum((s - mean) ** 2 for s in sizes) / n
        reversals = sum(
            1 for i in range(1, n)
            if h[i].direction != 0 and h[i - 1].direction != 0
            and h[i].direction != h[i - 1].direction
        )
        max_consecutive = cur = 0
        last_dir = 0
        for a in h:
            if a.direction != 0 and a.direction == last_dir:
                cur += 1
            elif a.direction != 0:
                cur = 1
            else:
                cur = 0
            last_dir = a.direction
            max_consecutive = max(max_consecutive, cur)
        holds = sum(1 for a in h if a.direction == 0)
        upper = self.target_latency * (1.0 + self.deadband)
        return {
            "rounds": n,
            "mean_size": round(mean, 2),
            "stddev": round(math.sqrt(var), 3),
            "reversals": reversals,
            "max_consecutive": max_consecutive,
            "hold_ratio": round(holds / n, 3),
            "mean_duration": round(sum(a.duration for a in h) / n, 4),
            "latency_violations": sum(1 for a in h if a.duration > upper),
        }
