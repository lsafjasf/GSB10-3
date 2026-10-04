"""复现基线：存在漂移缺陷的令牌桶限流器（请勿在生产使用）。

缺陷：每次调用 allow() 时，把距上次补充的时间差换算成令牌数后
做了 math.ceil 向上取整。请求间隔越小，单次取整带来的"白送"
令牌比例越高，长期运行时实际通过量会系统性地高于配置值。
"""

import math
import time


class BuggyTokenBucket:
    """令牌桶限流器（有缺陷版本，仅用于复现问题）。"""

    def __init__(self, rate, capacity, clock=time.monotonic):
        """
        :param rate: 每秒补充的令牌数（配置上限）
        :param capacity: 桶容量（允许的最大突发）
        :param clock: 可注入时钟，返回单调递增的秒数
        """
        if rate <= 0:
            raise ValueError("rate must be positive")
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.rate = float(rate)
        self.capacity = float(capacity)
        self.tokens = float(capacity)
        self.clock = clock
        self.last_refill = clock()

    def allow(self):
        """请求一个令牌，允许返回 True，拒绝返回 False。"""
        now = self.clock()
        elapsed = now - self.last_refill
        if elapsed > 0:
            # BUG: 向上取整。elapsed * rate 不足 1 个令牌时也补 1 个，
            # 高频请求下每次调用都白送 (1 - 小数部分) 个令牌。
            refill = math.ceil(elapsed * self.rate)
            self.tokens = min(self.capacity, self.tokens + refill)
            self.last_refill = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False
