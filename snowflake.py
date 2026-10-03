"""Snowflake 风格分布式唯一主键生成器（仅标准库，时间可注入）。

64 位布局（最高位恒为 0，保证为正整数）::

    0 | 41 位时间戳(毫秒, 相对自定义纪元) | 10 位节点号 | 12 位毫秒内序号

- 时间戳 41 位：相对纪元可用约 69.7 年。
- 节点号 10 位：最多 1024 个节点。
- 序号 12 位：每节点每毫秒最多 4096 个 ID，耗尽时等待下一毫秒，绝不回绕。

时钟回拨策略（默认拒绝，fail-fast）：
- 检测到当前时间 < 上次发号时间即抛 ClockMovedBackwardsError。
- 可选 rollback_tolerance_ms：回拨幅度在容忍范围内时等待时钟追平，
  超过容忍范围仍然拒绝。拒绝比等待更安全，详见 README。
"""

from __future__ import annotations

import threading
import time
from typing import Callable, NamedTuple

DEFAULT_EPOCH_MS = 1577836800000  # 2020-01-01T00:00:00Z


class ClockMovedBackwardsError(RuntimeError):
    """系统时钟回拨且超出容忍范围时抛出。"""


class TimestampOverflowError(OverflowError):
    """时间戳超出位宽（纪元起点过晚或时钟异常）时抛出。"""


class DecodedId(NamedTuple):
    timestamp_ms: int  # 绝对毫秒时间戳
    node_id: int
    sequence: int


class SnowflakeGenerator:
    """线程安全的单机发号器；多节点部署时每个节点使用不同的 node_id。"""

    NODE_BITS = 10
    SEQUENCE_BITS = 12
    TIMESTAMP_BITS = 41

    MAX_NODE_ID = (1 << NODE_BITS) - 1            # 1023
    MAX_SEQUENCE = (1 << SEQUENCE_BITS) - 1       # 4095
    MAX_TIMESTAMP = (1 << TIMESTAMP_BITS) - 1     # 相对纪元的毫秒上限

    _NODE_SHIFT = SEQUENCE_BITS
    _TIMESTAMP_SHIFT = SEQUENCE_BITS + NODE_BITS

    def __init__(
        self,
        node_id: int,
        *,
        epoch_ms: int = DEFAULT_EPOCH_MS,
        time_fn: Callable[[], float] = time.time,
        sleep_fn: Callable[[float], None] = time.sleep,
        rollback_tolerance_ms: int = 0,
    ) -> None:
        """
        :param node_id: 节点号，[0, 1023]，集群内必须唯一。
        :param epoch_ms: 自定义纪元（毫秒），用于压缩时间戳位宽。
        :param time_fn: 返回 Unix 秒（float）的时间函数，可注入假时钟。
        :param sleep_fn: 睡眠函数，可注入以便测试等待行为。
        :param rollback_tolerance_ms: 时钟回拨容忍毫秒数；0 表示一律拒绝。
        """
        if not 0 <= node_id <= self.MAX_NODE_ID:
            raise ValueError(
                f"node_id 必须在 [0, {self.MAX_NODE_ID}] 内，得到 {node_id}"
            )
        if rollback_tolerance_ms < 0:
            raise ValueError("rollback_tolerance_ms 不能为负")
        self.node_id = node_id
        self.epoch_ms = epoch_ms
        self._time_fn = time_fn
        self._sleep_fn = sleep_fn
        self._rollback_tolerance_ms = rollback_tolerance_ms

        self._lock = threading.Lock()
        self._last_ts_ms = -1
        self._sequence = 0

    # ------------------------------------------------------------------ API

    def next_id(self) -> int:
        """生成下一个全局唯一、本节点内严格单调递增的 64 位正整数。"""
        with self._lock:
            ts = self._now_ms()

            if ts < self._last_ts_ms:
                ts = self._handle_clock_rollback(ts)

            if ts == self._last_ts_ms:
                self._sequence += 1
                if self._sequence > self.MAX_SEQUENCE:
                    # 同一毫秒序号耗尽：等待下一毫秒，绝不回绕复用序号。
                    ts = self._wait_next_ms(self._last_ts_ms)
                    self._sequence = 0
            else:
                self._sequence = 0

            self._last_ts_ms = ts
            return self._compose(ts)

    @classmethod
    def decode(cls, id_: int, *, epoch_ms: int = DEFAULT_EPOCH_MS) -> DecodedId:
        """解析 ID，还原时间戳 / 节点号 / 序号。"""
        sequence = id_ & cls.MAX_SEQUENCE
        node_id = (id_ >> cls._NODE_SHIFT) & cls.MAX_NODE_ID
        timestamp_ms = (id_ >> cls._TIMESTAMP_SHIFT) + epoch_ms
        return DecodedId(timestamp_ms, node_id, sequence)

    # ------------------------------------------------------------- internal

    def _now_ms(self) -> int:
        return int(self._time_fn() * 1000)

    def _compose(self, ts_ms: int) -> int:
        offset = ts_ms - self.epoch_ms
        if not 0 <= offset <= self.MAX_TIMESTAMP:
            raise TimestampOverflowError(
                f"时间戳 {ts_ms} 超出 {self.TIMESTAMP_BITS} 位相对纪元的表示范围"
            )
        return (
            (offset << self._TIMESTAMP_SHIFT)
            | (self.node_id << self._NODE_SHIFT)
            | self._sequence
        )

    def _handle_clock_rollback(self, ts: int) -> int:
        drift = self._last_ts_ms - ts
        if drift > self._rollback_tolerance_ms:
            raise ClockMovedBackwardsError(
                f"时钟回拨 {drift} ms（容忍 {self._rollback_tolerance_ms} ms），拒绝发号"
            )
        # 小幅回拨：等待时钟追平到上次发号时间，再继续发号。
        while ts < self._last_ts_ms:
            self._sleep_fn(0.0005)  # 0.5 ms
            ts = self._now_ms()
        return ts

    def _wait_next_ms(self, last_ts: int) -> int:
        """自旋+睡眠直到时钟越过 last_ts，进入下一毫秒。"""
        ts = self._now_ms()
        while ts <= last_ts:
            self._sleep_fn(0.0005)
            ts = self._now_ms()
        return ts
