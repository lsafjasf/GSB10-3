"""事件流签名 + 时间戳校验库（仅标准库）。

设计要点：
- 签名内容：HMAC-SHA256(secret, f"{timestamp}.{body}")，时间戳纳入签名，
  防止攻击者截获后改时间戳重放。
- 时间注入：Verifier 接收 clock 可调用对象（默认 time.time），测试可注入假时钟。
- 重放防护：已接受的签名记入缓存，重复提交直接拒绝；缓存条目在
  时间窗口过后惰性清除（此时旧消息本来就会被窗口拒绝，清除是安全的）。
- 时钟回拨：采取保守策略（拒绝）。记录历史最大时钟值 max_clock_seen，
  有效当前时间 effective_now = max(clock(), max_clock_seen)。回拨后
  按回拨后时钟新签的消息会被判为“过旧”而拒绝。
  理由：若放宽（跟随回拨后的时钟），时间窗口会整体后移，之前已接受过的
  旧消息重新落入窗口，重放缓存又可能已清除，导致旧消息被二次接受。
  保守拒绝的代价仅是回拨期间新消息暂时不可用，可用性损失远小于
  重放被放行的安全风险。
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Union

# 校验结果原因码
OK = "ok"
BAD_SIGNATURE = "bad_signature"
TIMESTAMP_TOO_OLD = "timestamp_too_old"
TIMESTAMP_TOO_FAR_IN_FUTURE = "timestamp_too_far_in_future"
REPLAY_DETECTED = "replay_detected"
INVALID_TIMESTAMP = "invalid_timestamp"


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    reason: str


def compute_signature(secret: bytes, timestamp: int, body: Union[str, bytes]) -> str:
    """计算签名：HMAC-SHA256(secret, "{timestamp}.{body}")，hex 输出。"""
    if isinstance(body, str):
        body = body.encode("utf-8")
    payload = str(timestamp).encode("ascii") + b"." + body
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


class Verifier:
    """签名 + 时间戳 + 重放校验器。

    参数：
        secret: 共享密钥（bytes）。
        window_seconds: 允许的最大消息年龄（过去方向）。
        future_skew_seconds: 允许的最大未来偏移（发送方时钟略快的容忍）。
        clock: 返回当前 Unix 秒的可调用对象，可注入假时钟。
    """

    def __init__(
        self,
        secret: bytes,
        window_seconds: int = 300,
        future_skew_seconds: int = 60,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if window_seconds < 0 or future_skew_seconds < 0:
            raise ValueError("window/skew must be non-negative")
        self._secret = secret
        self._window = window_seconds
        self._future_skew = future_skew_seconds
        self._clock = clock
        # 已接受签名 -> 过期时间（effective_now 基准），用于重放识别
        self._seen: Dict[str, float] = {}
        # 历史最大时钟值，用于时钟回拨的保守处理
        self._max_clock_seen: float = float("-inf")

    def _effective_now(self) -> float:
        now = float(self._clock())
        if now > self._max_clock_seen:
            self._max_clock_seen = now
        return self._max_clock_seen

    def _purge_expired(self, effective_now: float) -> None:
        expired = [sig for sig, exp in self._seen.items() if exp < effective_now]
        for sig in expired:
            del self._seen[sig]

    def verify(self, timestamp: int, body: Union[str, bytes], signature: str) -> VerifyResult:
        """校验一条事件。通过则记录签名，后续相同签名视为重放。"""
        if not isinstance(timestamp, int) or isinstance(timestamp, bool):
            return VerifyResult(False, INVALID_TIMESTAMP)

        effective_now = self._effective_now()
        self._purge_expired(effective_now)

        # 1) 时间窗口校验（边界含端点：恰好等于边界视为有效）
        if timestamp < effective_now - self._window:
            return VerifyResult(False, TIMESTAMP_TOO_OLD)
        if timestamp > effective_now + self._future_skew:
            return VerifyResult(False, TIMESTAMP_TOO_FAR_IN_FUTURE)

        # 2) 签名校验（常数时间比较）
        expected = compute_signature(self._secret, timestamp, body)
        if not hmac.compare_digest(expected, signature):
            return VerifyResult(False, BAD_SIGNATURE)

        # 3) 重放校验：签名 + 时间戳绑定，同一签名只接受一次
        if signature in self._seen:
            return VerifyResult(False, REPLAY_DETECTED)

        # 记录签名，保留到窗口之外（此后该消息必被窗口拒绝，可安全清除）
        self._seen[signature] = effective_now + self._window + self._future_skew
        return VerifyResult(True, OK)

    @property
    def tracked_signatures(self) -> int:
        """当前重放缓存中的签名数量（测试/观测用）。"""
        self._purge_expired(self._effective_now())
        return len(self._seen)
