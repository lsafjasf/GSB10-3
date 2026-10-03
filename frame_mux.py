"""frame_mux —— 帧编解码与信道复用（仅标准库）。

帧格式（定长 9 字节头，全部大端）：

    +0  u16  magic     固定 0xA55A，用于流同步与错位检测
    +2  u8   type      帧类型（DATA/OPEN/CLOSE/PING）
    +3  u16  channel   信道号（0 保留给连接级控制帧，逻辑信道从 1 开始）
    +5  u32  length    负载字节数，必须与实际负载一致
    +9  ...  payload   长度恰为 length 字节

多条逻辑信道复用同一条字节流：发送方把不同信道的帧交错写入，
接收方用 FrameDecoder 解出帧，再交给 Demultiplexer 按信道号分发，
同一信道的负载严格按到达顺序拼接。
"""

from __future__ import annotations

import heapq
import struct
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

MAGIC = 0xA55A
HEADER_FORMAT = ">HBHI"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # 9
MAX_PAYLOAD = 16 * 1024 * 1024  # 解码侧单帧负载上限，防止异常 length 触发巨额分配

TYPE_DATA = 0x01
TYPE_OPEN = 0x02
TYPE_CLOSE = 0x03
TYPE_PING = 0x04
FRAME_TYPES = {
    TYPE_DATA: "DATA",
    TYPE_OPEN: "OPEN",
    TYPE_CLOSE: "CLOSE",
    TYPE_PING: "PING",
}

CONTROL_CHANNEL = 0  # 保留信道：连接级控制帧


# ---------------------------------------------------------------- 帧与异常

@dataclass(frozen=True)
class Frame:
    type: int
    channel: int
    payload: bytes

    @property
    def type_name(self) -> str:
        return FRAME_TYPES.get(self.type, "0x%02X" % self.type)


class FrameError(ValueError):
    """帧层错误的基类，offset 为错误在输入流中的字节偏移。"""

    def __init__(self, message: str, offset: int):
        super().__init__("%s (offset=%d)" % (message, offset))
        self.offset = offset


class BadMagicError(FrameError):
    """魔数不匹配：流在此处错位（常见于 length 声明偏小而错切后续字节）。"""


class TruncatedFrameError(FrameError):
    """帧被截断：声明的 length 大于实际剩余负载。"""

    def __init__(self, offset: int, declared: int, available: int,
                 message: Optional[str] = None):
        if message is None:
            message = (
                "truncated frame: declared length=%d but only %d payload "
                "byte(s) available" % (declared, available)
            )
        super().__init__(message, offset)
        self.declared = declared
        self.available = available


class PayloadTooLargeError(FrameError):
    """length 超过 MAX_PAYLOAD，offset 指向 length 字段本身。"""


def encode_frame(ftype: int, channel: int, payload: bytes = b"") -> bytes:
    """把一帧编码为字节串；length 字段由实际负载计算，保证一致。"""
    payload = bytes(payload)
    if not 0 <= ftype <= 0xFF:
        raise ValueError("frame type out of u8 range: %r" % (ftype,))
    if not 0 <= channel <= 0xFFFF:
        raise ValueError("channel out of u16 range: %r" % (channel,))
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(
            "payload too large: %d > MAX_PAYLOAD=%d" % (len(payload), MAX_PAYLOAD)
        )
    return struct.pack(HEADER_FORMAT, MAGIC, ftype, channel, len(payload)) + payload


def multiplex(frames) -> bytes:
    """把若干帧按给定顺序交错写入同一条流（复用）。"""
    return b"".join(
        encode_frame(f.type, f.channel, f.payload) if isinstance(f, Frame)
        else encode_frame(*f)
        for f in frames
    )


# ---------------------------------------------------------------- 流式解码器

class FrameDecoder:
    """增量式帧解码器：feed() 任意切片，凑齐即产出完整帧。

    - 头不足 9 字节：等待更多数据；finish() 时若仍有残留则报错并给出偏移。
    - length 与实际负载不一致（声明偏大）：凑不满即报 TruncatedFrameError，
      offset 指向该帧起始位置。
    - length 声明偏小：解码器按声明长度切帧，下一帧的魔数校验会在错位点
      抛出 BadMagicError，offset 指向错位位置。
    """

    def __init__(self, on_frame: Optional[Callable[[Frame], None]] = None):
        self._buf = bytearray()
        self._base = 0  # _buf 起点在整个流中的绝对偏移
        self._on_frame = on_frame

    @property
    def stream_offset(self) -> int:
        """已消费的字节数（即下一个帧的起始偏移）。"""
        return self._base

    def feed(self, data: bytes) -> List[Frame]:
        frames: List[Frame] = []
        self._buf += data
        while True:
            if len(self._buf) < HEADER_SIZE:
                break
            magic, ftype, channel, length = struct.unpack(
                HEADER_FORMAT, self._buf[:HEADER_SIZE]
            )
            if magic != MAGIC:
                raise BadMagicError(
                    "bad magic 0x%04X (expected 0x%04X): stream desynchronized"
                    % (magic, MAGIC),
                    self._base,
                )
            if length > MAX_PAYLOAD:
                raise PayloadTooLargeError(
                    "declared length=%d exceeds MAX_PAYLOAD=%d"
                    % (length, MAX_PAYLOAD),
                    self._base + 5,  # length 字段偏移
                )
            if len(self._buf) < HEADER_SIZE + length:
                break  # 负载未凑齐，等待后续 feed；finish() 时才判定截断
            payload = bytes(self._buf[HEADER_SIZE:HEADER_SIZE + length])
            del self._buf[:HEADER_SIZE + length]
            frame = Frame(ftype, channel, payload)
            self._base += HEADER_SIZE + length
            frames.append(frame)
            if self._on_frame is not None:
                self._on_frame(frame)
        return frames

    def finish(self) -> None:
        """输入结束时的收尾校验：缓冲区必须为空，否则说明末帧被截断。"""
        if not self._buf:
            return
        if len(self._buf) >= HEADER_SIZE:
            _, _, _, length = struct.unpack(HEADER_FORMAT, self._buf[:HEADER_SIZE])
            raise TruncatedFrameError(
                self._base, length, len(self._buf) - HEADER_SIZE
            )
        raise TruncatedFrameError(
            self._base, -1, -len(self._buf),
            message="truncated frame header: only %d of %d header byte(s) available"
                    % (len(self._buf), HEADER_SIZE),
        )


def decode_all(data: bytes) -> List[Frame]:
    """一次性解码整条流；末尾存在不完整帧时抛 TruncatedFrameError。"""
    decoder = FrameDecoder()
    frames = decoder.feed(data)
    decoder.finish()
    return frames


# ---------------------------------------------------------------- 信道号分配

@dataclass(frozen=True)
class PoolEvent:
    """一次分配/回收的状态变化记录。"""

    seq: int
    op: str  # "alloc" | "release"
    channel: int
    allocated: Tuple[int, ...]  # 操作完成后的已分配集合（升序快照）
    free_count: int             # 操作完成后的剩余可分配数量


class ChannelExhaustedError(RuntimeError):
    """信道号耗尽。"""


class ChannelPool:
    """信道号分配器：分配、回收、复用（总是回收最小空闲号）。

    每次 alloc/release 都会向 events 追加一条 PoolEvent，
    记录操作前后的状态变化，可直接作为“分配与回收的状态变化数据”。
    """

    def __init__(self, capacity: int, first_channel: int = 1):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        if not 0 <= first_channel <= 0xFFFF - capacity + 1:
            raise ValueError("channel range must fit in u16")
        self.capacity = capacity
        self.first_channel = first_channel
        self._free = [first_channel + i for i in range(capacity)]
        heapq.heapify(self._free)
        self._allocated: set = set()
        self.events: List[PoolEvent] = []

    def alloc(self) -> int:
        if not self._free:
            raise ChannelExhaustedError(
                "channel pool exhausted: capacity=%d, all %d channel(s) in use"
                % (self.capacity, len(self._allocated))
            )
        channel = heapq.heappop(self._free)
        self._allocated.add(channel)
        self._record("alloc", channel)
        return channel

    def release(self, channel: int) -> None:
        if channel not in self._allocated:
            raise ValueError("channel %d is not allocated" % channel)
        self._allocated.remove(channel)
        heapq.heappush(self._free, channel)
        self._record("release", channel)

    def _record(self, op: str, channel: int) -> None:
        self.events.append(
            PoolEvent(
                seq=len(self.events) + 1,
                op=op,
                channel=channel,
                allocated=tuple(sorted(self._allocated)),
                free_count=len(self._free),
            )
        )

    @property
    def allocated(self) -> Tuple[int, ...]:
        return tuple(sorted(self._allocated))

    @property
    def free_count(self) -> int:
        return len(self._free)


# ---------------------------------------------------------------- 解复用

@dataclass(frozen=True)
class ChunkRecord:
    """一次分发的记录：第 index 个到达的帧，属于哪个信道、带了什么负载。"""

    index: int
    channel: int
    ftype: int
    payload: bytes


class OrderAssertionError(AssertionError):
    pass


class Demultiplexer:
    """按信道号分发负载；同一信道的负载按到达顺序拼接。"""

    def __init__(self):
        self._buffers: Dict[int, bytearray] = {}
        self.arrival_log: List[ChunkRecord] = []

    def dispatch(self, frame: Frame) -> None:
        self._buffers.setdefault(frame.channel, bytearray()).extend(frame.payload)
        self.arrival_log.append(
            ChunkRecord(len(self.arrival_log), frame.channel, frame.type, frame.payload)
        )

    def feed(self, frames) -> None:
        for frame in frames:
            self.dispatch(frame)

    def payload(self, channel: int) -> bytes:
        """该信道目前已拼接好的负载（按到达顺序）。"""
        return bytes(self._buffers.get(channel, b""))

    @property
    def channels(self) -> Tuple[int, ...]:
        return tuple(sorted(self._buffers))

    def assert_channel_order(self, channel: int, expected_chunks) -> None:
        """顺序断言：该信道到达的负载分片必须与 expected_chunks 完全一致。

        同时校验到达日志中该信道的分片下标严格递增（无乱序、无丢失、无重复）。
        """
        expected = [bytes(c) for c in expected_chunks]
        records = [r for r in self.arrival_log if r.channel == channel]
        actual = [r.payload for r in records]
        indices = [r.index for r in records]
        if indices != sorted(indices) or len(set(indices)) != len(indices):
            raise OrderAssertionError(
                "channel %d arrival indices are not strictly increasing: %r"
                % (channel, indices)
            )
        if actual != expected:
            raise OrderAssertionError(
                "channel %d payload order mismatch:\n  expected=%r\n  actual  =%r"
                % (channel, expected, actual)
            )

    def assert_global_order(self, expected_channels) -> None:
        """全局顺序断言：所有帧的信道号到达序列必须与预期一致。"""
        actual = [r.channel for r in self.arrival_log]
        expected = list(expected_channels)
        if actual != expected:
            raise OrderAssertionError(
                "global arrival order mismatch:\n  expected=%r\n  actual  =%r"
                % (expected, actual)
            )
