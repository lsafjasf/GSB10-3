"""投递过程中的错误分类：retryable=True 才允许退避重试。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # 仅类型标注，避免运行时循环导入
    from .replies import SmtpReply


class SmtpDeliveryError(Exception):
    """所有投递错误的基类。stage 记录出错时所处的会话阶段。"""

    retryable = False

    def __init__(self, message: str, *, stage: str = "unknown"):
        super().__init__(message)
        self.stage = stage


class TransientFailure(SmtpDeliveryError):
    """4xx 临时拒绝 / 421：可按退避策略重试。"""

    retryable = True

    def __init__(self, message: str, *, reply: "Optional[SmtpReply]" = None,
                 stage: str = "unknown"):
        super().__init__(message, stage=stage)
        self.reply = reply


class PermanentFailure(SmtpDeliveryError):
    """5xx 永久拒绝：必须立刻中止，不得重试。"""

    retryable = False

    def __init__(self, message: str, *, reply: "Optional[SmtpReply]" = None,
                 stage: str = "unknown"):
        super().__init__(message, stage=stage)
        self.reply = reply


class ProtocolError(SmtpDeliveryError):
    """对端应答不符合 RFC（代码不一致、格式非法等），无法安全继续，按可重试处理。"""

    retryable = True


class ConnectionLost(SmtpDeliveryError):
    """连接在应答中途被对端关闭或发生 I/O 错误，按可重试处理。"""

    retryable = True


class SessionTimeout(SmtpDeliveryError):
    """会话整体超时（含读应答/握手超过 deadline），按可重试处理。"""

    retryable = True
