"""framemux — 帧编解码与信道复用（仅标准库）。

帧格式（大端，9 字节头 + 负载）::

    0           1           2      3        4        5..8         9..
    +-----------+-----------+------+--------+--------+------------+------...
    | MAGIC 0xC3| MAGIC 0x9E| TYPE | CHANNEL (2B BE)| LENGTH(4B BE)| PAYLOAD
    +-----------+-----------+------+--------+--------+------------+------...

- MAGIC  : 每帧起始魔数。若上一帧的 LENGTH 与实际负载不符，
           解码器会在期望位置看不到魔数，从而报出长度错误及字节偏移。
- TYPE   : 0x01 DATA / 0x02 OPEN / 0x03 CLOSE
- CHANNEL: 逻辑信道号，0 保留，可用范围 1..65535
- LENGTH : 负载字节数，必须与实际负载一致

运行自测:  python3 test_framemux.py
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

MAGIC = b"\xc3\x9e"
HEADER_SIZE = 9
MAX_CHANNEL = 0xFFFF
RESERVED_CHANNEL = 0

TYPE_DATA = 0x01
TYPE_OPEN = 0x02
TYPE_CLOSE = 0x03
TYPE_NAMES = {TYPE_DATA: "DATA", TYPE_OPEN: "OPEN", TYPE_CLOSE: "CLOSE"}

_HEADER = struct.Struct(">2sBHI")  # magic, type, channel, length


class FrameError(Exception):
    """帧解码错误，offset 为出错位置在流中的绝对字节偏移。"""

    def __init__(self, message: str, offset: int):
        super().__init__(f"{message} (offset={offset})")
        self.offset = offset


class TruncatedFrameError(FrameError):
    """流结束时仍存在不完整的帧。"""

    def __init__(self, offset: int, expected: int, actual: int):
        super().__init__(
            f"truncated frame: need {expected} bytes, got {actual}", offset
        )
        self.expected = expected
        self.actual = actual


class ChannelExhaustedError(Exception):
    """信道号耗尽。"""


class ChannelStateError(Exception):
    """信道状态非法（重复释放、向未打开的信道发送等）。"""


@dataclass(frozen=True)
class Frame:
    ftype: int
    channel: int
    payload: bytes

    @property
    def type_name(self) -> str:
        return TYPE_NAMES.get(self.ftype, f"UNKNOWN({self.ftype})")


def encode_frame(ftype: int, channel: int, payload: bytes) -> bytes:
    """编码一帧。LENGTH 字段取自 payload 实际长度，保证一致。"""
    if not 0 <= channel <= MAX_CHANNEL:
        raise ValueError(f"channel out of range: {channel}")
    if ftype not in TYPE_NAMES:
        raise ValueError(f"unknown frame type: {ftype}")
    payload = bytes(payload)
    return _HEADER.pack(MAGIC, ftype, channel, len(payload)) + payload


class FrameDecoder:
    """增量式帧解码器：feed() 喂入字节流，close() 校验无截断残留。"""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._consumed = 0  # 已消费字节数（即当前缓冲起点在流中的偏移）
        self._closed = False

    def feed(self, data: bytes) -> list[Frame]:
        if self._closed:
            raise FrameError("feed after close", self._consumed)
        self._buf += data
        frames: list[Frame] = []
        while True:
            if len(self._buf) < HEADER_SIZE:
                break
            magic, ftype, channel, length = _HEADER.unpack(
                self._buf[:HEADER_SIZE]
            )
            if magic != MAGIC:
                # 期望帧头位置没有魔数：上一帧 LENGTH 与实际负载不一致
                raise FrameError(
                    "length mismatch or stream desync: bad magic "
                    f"{magic.hex()} at frame boundary",
                    self._consumed,
                )
            if len(self._buf) < HEADER_SIZE + length:
                break  # 帧体未到齐，等待更多数据
            payload = bytes(self._buf[HEADER_SIZE : HEADER_SIZE + length])
            del self._buf[: HEADER_SIZE + length]
            self._consumed += HEADER_SIZE + length
            frames.append(Frame(ftype, channel, payload))
        return frames

    def close(self) -> None:
        """结束流；若残留不完整帧则抛 TruncatedFrameError（带偏移）。"""
        self._closed = True
        if not self._buf:
            return
        offset = self._consumed
        expected = HEADER_SIZE
        if len(self._buf) >= HEADER_SIZE:
            magic, _, _, length = _HEADER.unpack(self._buf[:HEADER_SIZE])
            if magic == MAGIC:
                expected = HEADER_SIZE + length
        raise TruncatedFrameError(offset, expected, len(self._buf))


class ChannelAllocator:
    """信道号分配器：分配最小空闲号，回收后可复用。0 号保留。"""

    def __init__(self, max_channel: int = MAX_CHANNEL) -> None:
        if max_channel < 1:
            raise ValueError("max_channel must be >= 1")
        self._max = max_channel
        self._allocated: set[int] = set()

    def allocate(self) -> int:
        for ch in range(1, self._max + 1):
            if ch not in self._allocated:
                self._allocated.add(ch)
                return ch
        raise ChannelExhaustedError(
            f"all {self._max} channel ids are in use"
        )

    def release(self, channel: int) -> None:
        if channel not in self._allocated:
            raise ChannelStateError(f"channel {channel} is not allocated")
        self._allocated.discard(channel)

    def is_allocated(self, channel: int) -> bool:
        return channel in self._allocated

    def snapshot(self) -> list[int]:
        """当前已分配信道号的有序列表（用于状态变化断言/展示）。"""
        return sorted(self._allocated)


@dataclass
class ChannelState:
    chunks: list[bytes] = field(default_factory=list)

    def reassembled(self) -> bytes:
        """按到达顺序拼接该信道的全部负载。"""
        return b"".join(self.chunks)


class Multiplexer:
    """信道复用器：一条字节流上承载多条逻辑信道。

    - open_channel()/close_channel() 管理信道号的分配与回收复用
    - send() 编码 DATA 帧（返回待发送字节）
    - feed() 解码对端字节流并按信道号分发，同信道按到达顺序追加
    """

    def __init__(self, max_channel: int = MAX_CHANNEL) -> None:
        self.allocator = ChannelAllocator(max_channel)
        self._decoder = FrameDecoder()
        self._channels: dict[int, ChannelState] = {}

    def open_channel(self) -> int:
        ch = self.allocator.allocate()
        self._channels[ch] = ChannelState()
        return ch

    def close_channel(self, channel: int) -> None:
        self.allocator.release(channel)
        self._channels.pop(channel, None)

    def send(self, channel: int, payload: bytes) -> bytes:
        if not self.allocator.is_allocated(channel):
            raise ChannelStateError(f"channel {channel} is not open")
        return encode_frame(TYPE_DATA, channel, payload)

    def feed(self, data: bytes) -> list[Frame]:
        frames = self._decoder.feed(data)
        for frame in frames:
            if frame.ftype == TYPE_DATA:
                state = self._channels.setdefault(frame.channel, ChannelState())
                state.chunks.append(frame.payload)  # 到达顺序即拼接顺序
        return frames

    def close(self) -> None:
        self._decoder.close()

    def chunks(self, channel: int) -> list[bytes]:
        return list(self._channels.get(channel, ChannelState()).chunks)

    def reassembled(self, channel: int) -> bytes:
        return self._channels.get(channel, ChannelState()).reassembled()
