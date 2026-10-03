"""可注入时钟：时间源与 sleep 都由调用方决定，测试可用假时钟零等待推进。"""

from __future__ import annotations

import time


class RealClock:
    """生产环境时钟，基于单调时间。"""

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class FakeClock:
    """测试用假时钟：sleep 只推进虚拟时间，不真正阻塞。"""

    def __init__(self, start: float = 1_000.0):
        self.now = float(start)

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("sleep 时长不能为负")
        self.now += seconds
