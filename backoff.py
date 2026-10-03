"""自旋与退避策略库（仅标准库，时钟可注入）。

策略接口：根据“这是第几次失败”给出下一步动作 Decision。
动作有三种：
  Action.SPIN  —— 纯自旋重试（适合极短临界区）
  Action.YIELD —— 让出时间片（达到退避上限后的退路之一）
  Action.SLEEP —— 阻塞 sleep(delay)（达到退避上限后的另一条退路）

本模块所有时间相关行为（sleep / yield / random）都可以注入，
便于在虚拟时钟下做确定性自测。
"""

from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional, Protocol

YieldFn = Callable[[], None]
SleepFn = Callable[[float], None]
RandomFn = Callable[[], float]


class Action(Enum):
    SPIN = "spin"
    YIELD = "yield"
    SLEEP = "sleep"


@dataclass(frozen=True)
class Decision:
    """第 N 次抢锁失败后应执行的动作；delay 仅 SLEEP 使用。"""

    action: Action
    delay: float = 0.0


class BackoffStrategy(Protocol):
    def decide(self, attempt: int) -> Decision:
        """attempt 从 1 开始，表示这是第几次抢锁失败。"""

    def reset(self) -> None:
        """每次 acquire 开始前调用（无状态策略可忽略）。"""


class FixedBackoff:
    """固定次数自旋：前 spins 次纯自旋，超过上限立即切换为让出/阻塞。"""

    def __init__(
        self,
        spins: int = 16,
        then: Action = Action.YIELD,
        block_delay: float = 1e-3,
    ) -> None:
        if spins < 0:
            raise ValueError("spins 必须 >= 0")
        if then not in (Action.YIELD, Action.SLEEP):
            raise ValueError("达到上限后的退路只能是 YIELD 或 SLEEP")
        if block_delay < 0:
            raise ValueError("block_delay 必须 >= 0")
        self.spins = spins
        self.then = then
        self.block_delay = block_delay

    def decide(self, attempt: int) -> Decision:
        if attempt <= self.spins:
            return Decision(Action.SPIN)
        return Decision(self.then, self.block_delay if self.then is Action.SLEEP else 0.0)

    def reset(self) -> None:
        return None


class ExponentialBackoff:
    """指数退避：delay = base * factor**(attempt-1)，封顶 cap。

    未到 cap：阻塞 sleep(增长中的 delay)；
    一旦指数值超过 cap：切换为让出（YIELD）或固定阻塞（SLEEP(cap)）。
    """

    def __init__(
        self,
        base: float = 1e-6,
        factor: float = 2.0,
        cap: float = 1e-3,
        then: Action = Action.YIELD,
    ) -> None:
        if base <= 0 or factor <= 1.0 or cap <= 0:
            raise ValueError("需要 base > 0、factor > 1、cap > 0")
        if then not in (Action.YIELD, Action.SLEEP):
            raise ValueError("达到上限后的退路只能是 YIELD 或 SLEEP")
        self.base = base
        self.factor = factor
        self.cap = cap
        self.then = then
        self.switch_at = self._compute_switch_point()

    def _compute_switch_point(self) -> int:
        # 最小的 attempt n，使得 base * factor**(n-1) > cap（不会溢出）
        n = math.floor(math.log(self.cap / self.base, self.factor)) + 2
        while self.base * self.factor ** (n - 2) > self.cap:
            n -= 1
        while self.base * self.factor ** (n - 1) <= self.cap:
            n += 1
        return n

    def decide(self, attempt: int) -> Decision:
        if attempt < self.switch_at:
            return Decision(Action.SLEEP, self.base * self.factor ** (attempt - 1))
        return Decision(self.then, self.cap if self.then is Action.SLEEP else 0.0)

    def reset(self) -> None:
        return None


class ExponentialJitterBackoff:
    """带随机抖动的指数退避（full jitter）。

    delay = random_fn() * min(cap, base * factor**(attempt-1))，
    即延迟严格落在 [0, 指数上限] 内，绝不会突破 cap；
    一旦指数值超过 cap：无论随机数取什么，都切换为让出/阻塞。
    random_fn 默认 random.random，可注入以便测试边界值。
    """

    def __init__(
        self,
        base: float = 1e-6,
        factor: float = 2.0,
        cap: float = 1e-3,
        then: Action = Action.YIELD,
        random_fn: Optional[RandomFn] = None,
    ) -> None:
        if base <= 0 or factor <= 1.0 or cap <= 0:
            raise ValueError("需要 base > 0、factor > 1、cap > 0")
        if then not in (Action.YIELD, Action.SLEEP):
            raise ValueError("达到上限后的退路只能是 YIELD 或 SLEEP")
        self.base = base
        self.factor = factor
        self.cap = cap
        self.then = then
        self.random_fn: RandomFn = random_fn or random.random
        self.switch_at = self._compute_switch_point()

    def _compute_switch_point(self) -> int:
        n = math.floor(math.log(self.cap / self.base, self.factor)) + 2
        while self.base * self.factor ** (n - 2) > self.cap:
            n -= 1
        while self.base * self.factor ** (n - 1) <= self.cap:
            n += 1
        return n

    def decide(self, attempt: int) -> Decision:
        if attempt < self.switch_at:
            raw = self.base * self.factor ** (attempt - 1)
            return Decision(Action.SLEEP, self.random_fn() * raw)
        return Decision(self.then, self.cap if self.then is Action.SLEEP else 0.0)

    def reset(self) -> None:
        return None


def default_yield() -> None:
    # time.sleep(0) 在 CPython 上等价于让出 GIL/时间片，标准库中可移植的选择。
    time.sleep(0)


class SpinLock:
    """基于非阻塞抢锁的自旋锁，退避策略可插拔，时钟/让出原语可注入。

    acquire() 返回总尝试次数（1 表示一次成功，即无竞争）。
    """

    def __init__(
        self,
        strategy: BackoffStrategy,
        sleep_fn: SleepFn = time.sleep,
        yield_fn: YieldFn = default_yield,
    ) -> None:
        self._lock = threading.Lock()
        self._strategy = strategy
        self._sleep_fn = sleep_fn
        self._yield_fn = yield_fn
        self.owner: Optional[int] = None
        self.holder_count = 0  # 自测用：当前持有者数量，必须恒为 0 或 1
        self._meta_lock = threading.Lock()

    def acquire(self) -> int:
        self._strategy.reset()
        attempts = 0
        while True:
            attempts += 1
            if self._lock.acquire(blocking=False):
                with self._meta_lock:
                    self.holder_count += 1
                    self.owner = threading.get_ident()
                return attempts
            decision = self._strategy.decide(attempts)
            if decision.action is Action.SLEEP:
                self._sleep_fn(decision.delay)
            elif decision.action is Action.YIELD:
                self._yield_fn()
            # SPIN：立即重试

    def release(self) -> None:
        with self._meta_lock:
            self.holder_count -= 1
            self.owner = None
        self._lock.release()

    def __enter__(self) -> "SpinLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


STRATEGIES = {
    "fixed": FixedBackoff,
    "exponential": ExponentialBackoff,
    "exponential_jitter": ExponentialJitterBackoff,
}
