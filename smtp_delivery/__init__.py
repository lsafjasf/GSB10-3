"""SMTP 投递会话状态机：库入口。"""

from .clock import FakeClock, RealClock
from .delivery import AttemptRecord, DeliveryResult, deliver
from .errors import (ConnectionLost, PermanentFailure, ProtocolError,
                     SessionTimeout, SmtpDeliveryError, TransientFailure)
from .policy import BackoffPolicy, DeliveryPolicy, TlsMode
from .replies import SmtpReply, read_reply
from .session import SessionEvent, SmtpSession, Stage
from .transport import SocketTransport, Transport

__all__ = [
    "deliver", "DeliveryPolicy", "BackoffPolicy", "TlsMode",
    "DeliveryResult", "AttemptRecord",
    "SmtpSession", "Stage", "SessionEvent",
    "SmtpReply", "read_reply",
    "SocketTransport", "Transport",
    "FakeClock", "RealClock",
    "SmtpDeliveryError", "TransientFailure", "PermanentFailure",
    "ProtocolError", "ConnectionLost", "SessionTimeout",
]
