"""投递策略：TLS 策略、退避参数、超时预算。"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Callable, Optional


class TlsMode(enum.Enum):
    OFF = "off"        # 始终明文；对端 530 要求加密时按永久拒绝中止
    PREFER = "prefer"  # 对端支持 STARTTLS 就升级；临时不可用可退回明文，中途被要求仍会升级
    REQUIRE = "require"  # 必须加密；对端不支持或升级失败即视为临时失败，稍后重试


@dataclass(frozen=True)
class BackoffPolicy:
    """指数退避 + 向下等值抖动；随机源可注入，测试可完全确定。

    delay(attempt) ∈ [raw*(1-jitter), raw]，
    raw = min(maximum, base * factor**(attempt-1))。
    """

    base: float = 2.0
    factor: float = 2.0
    maximum: float = 60.0
    jitter: float = 0.2

    def delay(self, attempt: int,
              rand: Optional[Callable[[], float]] = None) -> float:
        if attempt < 1:
            raise ValueError("attempt 从 1 开始计数")
        raw = min(self.maximum, self.base * (self.factor ** (attempt - 1)))
        if self.jitter <= 0:
            return round(raw, 6)
        if rand is None:
            import random
            rand = random.random
        spread = raw * self.jitter
        return round(raw - spread + spread * rand(), 6)


@dataclass(frozen=True)
class DeliveryPolicy:
    helo_name: str = "mailer.local"
    tls: TlsMode = TlsMode.PREFER
    session_timeout: float = 60.0             # 单次会话（单次尝试）的整体超时
    overall_timeout: Optional[float] = None  # 含重试退避在内的总预算；None 表示不限
    max_attempts: int = 5
    backoff: BackoffPolicy = BackoffPolicy()
    ehlo_fallback: bool = True                # EHLO 被 5xx 拒绝时退回 HELO 再试一次
