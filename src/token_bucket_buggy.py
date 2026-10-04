"""有缺陷的令牌桶限流器（复现基线，请勿在生产使用）。

缺陷：refill 时用 int(now) 截断时间戳，导致同一秒内的小数部分
 elapsed 被反复重复计入，长时间运行后实际放行量明显超过配置值。
"""

import time


class TokenBucket:
    def __init__(self, rate_per_sec, capacity, clock=None):
        self._rate = float(rate_per_sec)
        self._capacity = float(capacity)
        self._tokens = float(capacity)
        self._clock = clock or time.monotonic
        self._last = self._clock()

    def _refill(self, now):
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            # BUG: 时间戳被取整到整秒，丢失了掉的小数部分会在
            # 同一秒内的后续请求中被重复计算为 elapsed。
            self._last = int(now)

    def allow(self):
        now = self._clock()
        self._refill(now)
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    @property
    def tokens(self):
        return self._tokens
