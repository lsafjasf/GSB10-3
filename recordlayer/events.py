"""解析器按记录类型分发后产出的事件对象。

每条事件都保留原始字节（``raw``），告警事件额外保留未解释的
原始码值（``level`` / ``description``），不会和普通数据混淆。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Record:
    """一条原始记录帧（未按类型分发前的形态）。"""

    content_type: int
    version: tuple
    body: bytes


@dataclass(frozen=True)
class HandshakeMessage:
    """一条重组完成的握手消息（可能由多条记录的分片拼成）。"""

    msg_type: int
    message_seq: int
    body: bytes
    raw: bytes  # 含握手消息头的完整原始字节


@dataclass(frozen=True)
class Alert:
    """告警记录。``level`` / ``description`` 为原始码值，未做白名单过滤。"""

    level: int
    description: int
    raw: bytes

    @property
    def level_name(self):
        return {1: "warning", 2: "fatal"}.get(self.level, "unknown(%d)" % self.level)


@dataclass(frozen=True)
class ChangeCipherSpec:
    """ChangeCipherSpec 记录，协议规定载荷恒为单字节 0x01。"""

    raw: bytes


@dataclass(frozen=True)
class ApplicationData:
    """普通应用数据记录。"""

    data: bytes


@dataclass(frozen=True)
class UnknownRecord:
    """未知记录类型：保留类型码与原始分片，交给上层决定如何处理。"""

    content_type: int
    version: tuple
    body: bytes
