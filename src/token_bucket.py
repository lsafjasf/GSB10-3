"""修复后的令牌桶限流器（仅标准库，时钟可注入）。

修复点：
1. refill 使用精确的浮点时间戳，不做 int() 截断，
   每段真实流逝的时间只被计入一次，杜绝重复补充。
2. 时钟回拨（now < last）时不补充令牌、不回退 last，
   回拨期间不会产生额外额度。
"""

import time


class TokenBucket:
    def __init__(self, rate_per_sec, capacity, clock=None):
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be positive")
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._rate = float(rate_per_sec)
        self._capacity = float(capacity)
        self._tokens = float(capacity)
        self._clock = clock or time.monotonic
        self._last = float(self._clock())

    def _refill(self, now):
        elapsed = now - self._last
        if elapsed <= 0:
            # 时钟回拨或同一时刻重复调用：不补充、不移动基准点。
            return
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last = now

    def allow(self):
        now = float(self._clock())
        self._refill(now)
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    @property
    def tokens(self):
        return self._tokens
