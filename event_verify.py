"""事件流签名 + 时间戳 + 防重放校验（仅标准库）。

签名内容: HMAC_SHA256(secret, f"{timestamp}.{payload}")
时间戳与签名绑定，篡改任一方都会导致签名不匹配。
时间通过可注入的 clock 函数获取，便于测试。
"""

import hashlib
import hmac
import time
from typing import Callable, Dict, Optional, Tuple

OK = "ok"
ERR_BAD_SIGNATURE = "bad_signature"
ERR_TOO_OLD = "timestamp_too_old"
ERR_TOO_NEW = "timestamp_too_new"
ERR_REPLAY = "signature_replay"
ERR_CLOCK_ROLLBACK = "clock_rollback"


def sign(secret: bytes, payload: str, timestamp: int) -> str:
    """生成与时间戳绑定的十六进制 HMAC-SHA256 签名。"""
    mac = hmac.new(secret, f"{timestamp}.{payload}".encode("utf-8"),
                   hashlib.sha256)
    return mac.hexdigest()


class SignatureVerifier:
    def __init__(
        self,
        secret: bytes,
        max_age: int = 300,
        max_future_skew: int = 60,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        """
        max_age: 允许的最大消息年龄（秒），下界闭区间。
        max_future_skew: 允许的最大时钟前偏（秒），上界闭区间。
        clock: 可注入的当前时间（epoch 秒），默认 time.time。
        """
        self._secret = secret
        self.max_age = max_age
        self.max_future_skew = max_future_skew
        self._clock = clock or time.time
        # 已使用签名 -> 该签名对应的过期时刻(now)，过期后可清理。
        self._used: Dict[str, float] = {}
        # 观测到的最大时钟读数，用于检测时钟回拨。
        self._high_water: Optional[float] = None

    def verify(
        self,
        payload: str,
        timestamp: int,
        signature: str,
        now: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """校验一条消息，返回 (是否通过, 原因码)。

        检查顺序: 时钟回拨 -> 时间窗口 -> 签名 -> 重放。
        任何一项不通过都拒绝，且不把失败签名写入重放缓存。
        """
        current = self._clock() if now is None else now

        # 1) 时钟回拨：保守处理，直接拒绝（见模块说明）。
        if self._high_water is not None and current < self._high_water:
            return False, ERR_CLOCK_ROLLBACK
        self._high_water = current

        # 2) 时间窗口（边界为闭区间）:
        #    now - max_age <= ts <= now + max_future_skew
        if timestamp < current - self.max_age:
            return False, ERR_TOO_OLD
        if timestamp > current + self.max_future_skew:
            return False, ERR_TOO_NEW

        # 3) 签名校验（常量时间比较）。
        expected = sign(self._secret, payload, timestamp)
        if not hmac.compare_digest(expected, signature):
            return False, ERR_BAD_SIGNATURE

        # 4) 重放校验：同一签名在其存活期内只能接受一次。
        self._prune(current)
        if signature in self._used:
            return False, ERR_REPLAY
        self._used[signature] = current

        return True, OK

    def _prune(self, current: float) -> None:
        dead = current - self.max_age
        for sig, seen_at in list(self._used.items()):
            if seen_at < dead:
                del self._used[sig]
