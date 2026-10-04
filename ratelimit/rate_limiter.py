"""修复后的令牌桶限流器。

根因（见 REPORT.md）：旧实现在每次 allow() 时用 math.ceil 对补充令牌数
向上取整，请求间隔越小，单次白送的令牌比例越高，导致长期运行时实际
通过量系统性地超过配置值（1ms 间隔、rate=100/s 时高达 10 倍）。

修复要点：
1. 补充令牌保留小数（不做任何取整），分数令牌在桶中累积，
   长期平均严格等于 rate。
2. 时钟回拨（elapsed <= 0）时不补充令牌、也不回退 last_refill，
   即 fail-closed：回拨期间宁可少放也不多放，且不会在时钟恢复后
   重复发放同一段时间对应的令牌。
"""

import threading
import time


class TokenBucket:
    """令牌桶限流器。

    :param rate: 每秒补充的令牌数（配置上限）
    :param capacity: 桶容量（允许的最大突发）
    :param clock: 可注入时钟，返回秒数（默认 time.monotonic）
    """

    def __init__(self, rate, capacity, clock=time.monotonic):
        if rate <= 0:
            raise ValueError("rate must be positive")
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.rate = float(rate)
        self.capacity = float(capacity)
        self.tokens = float(capacity)
        self.clock = clock
        self.last_refill = clock()
        self._lock = threading.Lock()

    def _refill(self, now):
        elapsed = now - self.last_refill
        if elapsed <= 0:
            # 同一时刻或时钟回拨：不补充，也不移动 last_refill，
            # 避免时钟恢复后重复发放。
            return
        # 关键修复：保留小数令牌，不取整。
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_refill = now

    def allow(self):
        """请求一个令牌，允许返回 True，拒绝返回 False。"""
        with self._lock:
            self._refill(self.clock())
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True
            return False
