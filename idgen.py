"""Snowflake 风格的分布式有序主键生成器（仅依赖标准库，时间可注入）。

ID 为 64 位正整数，位布局（高位 -> 低位）:

    1 bit  符号位，恒为 0（保证 ID 为正数）
   41 bit  毫秒时间戳（相对自定义纪元 EPOCH_MS，约 69 年）
   10 bit  节点号 node_id（0..1023）
   12 bit  同一毫秒内的序号 sequence（0..4095）

数值大小顺序 == (时间, 节点, 序号) 的字典序，因此同一节点生成的 ID 严格单调递增。

关键语义:
- 同一毫秒序号耗尽（4096 个已用完）时阻塞等待到下一毫秒，绝不回绕
  （回绕会在相同时间戳下产生重复 ID）。
- 检测到时钟回拨（now < 上次发号时间）时:
    * 回拨幅度 <= max_backward_ms（默认 5ms，常见 NTP/虚拟机抖动）:
      等待时钟追平后继续；
    * 超过阈值: 抛出 ClockMovedBackwardsError 拒绝发号。
  更安全的策略是“拒绝”（见模块文档字符串末尾与 README），等待只是对微小
  抖动的可用性折中；把 max_backward_ms 设为 0 即永远拒绝。
"""

from __future__ import annotations

import threading
import time
from typing import Callable, NamedTuple, Tuple

# 自定义纪元: 2024-01-01T00:00:00Z
EPOCH_MS = 1704067200000

NODE_ID_BITS = 10
SEQUENCE_BITS = 12

MAX_NODE_ID = (1 << NODE_ID_BITS) - 1          # 1023
MAX_SEQUENCE = (1 << SEQUENCE_BITS) - 1       # 4095
MAX_ELAPSED_MS = (1 << 41) - 1

NODE_ID_SHIFT = SEQUENCE_BITS                 # 12
TIMESTAMP_SHIFT = SEQUENCE_BITS + NODE_ID_BITS  # 22

# 序号耗尽 / 等时钟追平时，每次自旋睡眠的粒度
_SPIN_SLEEP_MS = 0.0005


class ClockMovedBackwardsError(RuntimeError):
    """检测到时钟回拨且幅度超过可容忍阈值，拒绝发号。"""

    def __init__(self, last_ms: int, now_ms: int):
        self.last_ms = last_ms
        self.now_ms = now_ms
        self.drift_ms = last_ms - now_ms
        super().__init__(
            f"clock moved backwards by {self.drift_ms} ms "
            f"(last={last_ms}, now={now_ms}); refusing to issue an id"
        )


class Parts(NamedTuple):
    timestamp_ms: int
    node_id: int
    sequence: int


def default_time_ms() -> int:
    """默认时钟：返回 Unix 毫秒时间戳。"""
    return int(time.time() * 1000)


def decode(value: int) -> Parts:
    """把 ID 拆回 (毫秒时间戳, 节点号, 序号)。"""
    return Parts(
        timestamp_ms=(value >> TIMESTAMP_SHIFT) + EPOCH_MS,
        node_id=(value >> NODE_ID_SHIFT) & MAX_NODE_ID,
        sequence=value & MAX_SEQUENCE,
    )


class IdGenerator:
    """线程安全的发号器。每个节点（进程/机器）持有一个实例，node_id 全局唯一。

    time_ms / sleep 均可注入，便于测试：注入的 sleep 在被调用时可以顺便推进
    虚拟时钟，从而确定性地验证“等待”行为。
    """

    def __init__(
        self,
        node_id: int,
        *,
        time_ms: Callable[[], int] = default_time_ms,
        sleep: Callable[[float], None] = time.sleep,
        epoch_ms: int = EPOCH_MS,
        max_backward_ms: int = 5,
    ) -> None:
        if not 0 <= node_id <= MAX_NODE_ID:
            raise ValueError(f"node_id must be in [0, {MAX_NODE_ID}], got {node_id}")
        if max_backward_ms < 0:
            raise ValueError("max_backward_ms must be >= 0")
        self.node_id = node_id
        self._time_ms = time_ms
        self._sleep = sleep
        self._epoch_ms = epoch_ms
        self._max_backward_ms = max_backward_ms
        self._lock = threading.Lock()
        self._last_ms = -1
        self._sequence = 0

    # 方便测试/监控：持锁读取快照
    @property
    def last_ms(self) -> int:
        with self._lock:
            return self._last_ms

    def next_id(self) -> int:
        with self._lock:
            now = self._time_ms()

            # ---- 1. 时钟回拨处理 ----
            if now < self._last_ms:
                drift = self._last_ms - now
                if drift > self._max_backward_ms:
                    # 大幅度回拨：拒绝发号（fail-fast，最安全）
                    raise ClockMovedBackwardsError(self._last_ms, now)
                # 小幅度回拨：等待时钟追平上次发号时间。追平后按普通流程走：
                #   now == _last_ms 时安全地延续序号，now > _last_ms 时序号归零。
                self._wait_until(lambda t: t >= self._last_ms)
                now = self._time_ms()
                if now < self._last_ms:
                    # 等待后仍未恢复（例如时钟再次跳变），拒绝而不是冒险发号
                    raise ClockMovedBackwardsError(self._last_ms, now)

            # ---- 2. 同毫秒序号耗尽：等待下一毫秒，绝不回绕 ----
            if now == self._last_ms:
                if self._sequence >= MAX_SEQUENCE:
                    self._wait_until(lambda t: t > self._last_ms)
                    now = self._time_ms()

            # ---- 3. 取序号并落状态 ----
            if now > self._last_ms:
                self._sequence = 0
            elif now == self._last_ms:
                self._sequence += 1
            else:  # 理论不可达：前面的检查保证 now >= _last_ms
                raise ClockMovedBackwardsError(self._last_ms, now)

            elapsed = now - self._epoch_ms
            if elapsed < 0:
                raise ValueError(f"time {now} is before epoch {self._epoch_ms}")
            if elapsed > MAX_ELAPSED_MS:
                raise ValueError("timestamp overflow: 41-bit elapsed ms exhausted")

            self._last_ms = now
            return (
                (elapsed << TIMESTAMP_SHIFT)
                | (self.node_id << NODE_ID_SHIFT)
                | self._sequence
            )

    def _wait_until(self, predicate: Callable[[int], bool]) -> None:
        """持锁自旋等待，直到当前时间满足条件。sleep 可被注入。"""
        current = self._time_ms()
        while not predicate(current):
            self._sleep(_SPIN_SLEEP_MS)
            current = self._time_ms()
