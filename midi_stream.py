"""MIDI 事件流解析与多轨道合并（仅标准库）。

解析粒度为“轨道事件流”（SMF 中 MTrk chunk 的 payload），并提供
文件级解析（parse_smf）与多轨道合并（merge_tracks）。

解析规则（与 reference.py 的逐字节参考实现严格一致）：

* 增量时间为变长量（VLQ），长度不限（支持超长增量时间），
  绝对时间由增量累加得到。
* 通道消息（0x80-0xEF）支持运行状态（running status）：
  数据字节（<0x80）出现在通道状态之后时复用该状态。
* 0xF0/0xF7 为系统专有事件（VLQ 长度 + 负载）；0xFF 为元事件
  （类型 1 字节 + VLQ 长度 + 负载）。
* 系统公共消息 0xF1/0xF2/0xF3/0xF6 与实时消息 0xF8-0xFE
  按固定长度解析；实时消息不清除运行状态，其余 0xF0 以上
  状态字节都会清除运行状态。
* 不认识的字节（如 0xF4/0xF5/0xFD、无运行状态时的孤立数据
  字节）原样保留为 kind='unknown' 的单字节事件，不丢弃。
* 事件流被截断时不抛异常：产出 kind='truncated' 的收尾事件，
  raw 字段保留剩余原始字节。
* 通道数据字节不校验 <0x80，按原样消费（确定性规则）。

合并规则：按 (abs_time, track, order) 升序排序。即同一绝对时刻，
轨道号小者优先；同一轨道内保持原始先后顺序。每个事件都带有
track（来源轨道号）与 order（轨道内序号）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

# 通道消息高半字节 -> 数据字节数
CHANNEL_DATA_LEN = {
    0x80: 2,  # Note Off
    0x90: 2,  # Note On
    0xA0: 2,  # Poly Aftertouch
    0xB0: 2,  # Control Change
    0xC0: 1,  # Program Change
    0xD0: 1,  # Channel Aftertouch
    0xE0: 2,  # Pitch Bend
}

# 系统公共/实时消息 -> 数据字节数（0xF0/0xF7/0xFF 单独处理）
SYSTEM_DATA_LEN = {
    0xF1: 1, 0xF2: 2, 0xF3: 1, 0xF6: 0,
    0xF8: 0, 0xF9: 0, 0xFA: 0, 0xFB: 0, 0xFC: 0, 0xFE: 0,
}

# 实时消息：不清除运行状态
REALTIME = frozenset({0xF8, 0xF9, 0xFA, 0xFB, 0xFC, 0xFE})


@dataclass
class Event:
    delta: Optional[int]       # 增量时间；VLQ 本身被截断时为 None
    abs_time: int              # 绝对时间（增量累加）
    track: int                 # 来源轨道号
    order: int                 # 轨道内序号
    kind: str                  # channel/sysex/meta/system/unknown/truncated
    status: Optional[int]      # 状态字节（运行状态事件为其复用的状态）
    meta_type: Optional[int]   # 元事件类型，非元事件为 None
    data: bytes                # 负载（不含状态字节与元事件类型字节）
    raw: bytes                 # 原始字节（不含增量时间 VLQ）

    def signature(self) -> tuple:
        """用于与参考实现对比的规范化元组。"""
        return (self.delta, self.abs_time, self.track, self.order,
                self.kind, self.status, self.meta_type,
                bytes(self.data), bytes(self.raw))


def read_vlq(buf: bytes, pos: int) -> Tuple[Optional[int], int]:
    """读取变长量，返回 (value, new_pos)；数据耗尽时 value 为 None。"""
    value = 0
    while True:
        if pos >= len(buf):
            return None, pos
        b = buf[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if not (b & 0x80):
            return value, pos


def encode_vlq(value: int) -> bytes:
    """把非负整数编码为变长量（生成测试数据用）。"""
    if value < 0:
        raise ValueError("VLQ 只支持非负整数")
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.append(0x80 | (value & 0x7F))
        value >>= 7
    out.reverse()
    return bytes(out)


def parse_track(buf: bytes, track: int = 0) -> List[Event]:
    """解析单条轨道的事件流，返回带绝对时间的事件列表。"""
    events: List[Event] = []
    pos = 0
    time = 0
    running: Optional[int] = None
    n = len(buf)

    def emit(delta, kind, status, meta_type, data, raw):
        events.append(Event(delta, time, track, len(events),
                            kind, status, meta_type, data, raw))

    while pos < n:
        vlq_start = pos
        delta, pos = read_vlq(buf, pos)
        if delta is None:  # 增量时间 VLQ 被截断
            emit(None, 'truncated', None, None, b'', buf[vlq_start:pos])
            break
        time += delta
        if pos >= n:  # 只有增量时间，没有事件本体
            emit(delta, 'truncated', None, None, b'', b'')
            break
        ev_start = pos
        b = buf[pos]
        if b < 0x80:
            if running is None:
                emit(delta, 'unknown', None, None, b'', buf[pos:pos + 1])
                pos += 1
            else:
                need = CHANNEL_DATA_LEN[running & 0xF0]
                end = pos + need
                if end > n:
                    emit(delta, 'truncated', running, None, buf[pos:], buf[pos:])
                    break
                emit(delta, 'channel', running, None, buf[pos:end], buf[pos:end])
                pos = end
            continue
        pos += 1
        if b < 0xF0:  # 通道消息（带状态字节）
            running = b
            need = CHANNEL_DATA_LEN[b & 0xF0]
            end = pos + need
            if end > n:
                emit(delta, 'truncated', b, None, buf[pos:], buf[ev_start:])
                break
            emit(delta, 'channel', b, None, buf[pos:end], buf[ev_start:end])
            pos = end
        elif b in (0xF0, 0xF7):  # 系统专有事件
            running = None
            length, pos2 = read_vlq(buf, pos)
            if length is None:
                emit(delta, 'truncated', b, None, b'', buf[ev_start:])
                break
            pos = pos2
            end = pos + length
            if end > n:
                emit(delta, 'truncated', b, None, buf[pos:], buf[ev_start:])
                break
            emit(delta, 'sysex', b, None, buf[pos:end], buf[ev_start:end])
            pos = end
        elif b == 0xFF:  # 元事件
            running = None
            if pos >= n:
                emit(delta, 'truncated', b, None, b'', buf[ev_start:])
                break
            mtype = buf[pos]
            pos += 1
            length, pos2 = read_vlq(buf, pos)
            if length is None:
                emit(delta, 'truncated', b, mtype, b'', buf[ev_start:])
                break
            pos = pos2
            end = pos + length
            if end > n:
                emit(delta, 'truncated', b, mtype, buf[pos:], buf[ev_start:])
                break
            emit(delta, 'meta', b, mtype, buf[pos:end], buf[ev_start:end])
            pos = end
        elif b in SYSTEM_DATA_LEN:
            if b not in REALTIME:
                running = None
            need = SYSTEM_DATA_LEN[b]
            end = pos + need
            if end > n:
                emit(delta, 'truncated', b, None, buf[pos:], buf[ev_start:])
                break
            emit(delta, 'system', b, None, buf[pos:end], buf[ev_start:end])
            pos = end
        else:  # 0xF4/0xF5/0xFD 等未定义状态：原样保留
            running = None
            emit(delta, 'unknown', b, None, b'', buf[ev_start:pos])
    return events


def merge_tracks(tracks: List[List[Event]]) -> List[Event]:
    """把多条轨道合并为按绝对时间排序的事件流。

    排序键为 (abs_time, track, order)：同一时刻轨道号小者优先，
    同轨道内保持原始顺序。规则确定，可重复。
    """
    merged: List[Event] = []
    for evs in tracks:
        merged.extend(evs)
    merged.sort(key=lambda e: (e.abs_time, e.track, e.order))
    return merged


def parse_smf(data: bytes):
    """解析标准 MIDI 文件，返回 (header, tracks)。

    header 为 dict(format=, ntrks=, division=)。
    未知 chunk 跳过；chunk 被截断时按可用字节解析，不抛异常。
    """
    if len(data) < 14 or data[:4] != b'MThd':
        raise ValueError('不是合法的 SMF 数据（缺少 MThd 头）')
    hlen = int.from_bytes(data[4:8], 'big')
    if hlen < 6 or len(data) < 8 + hlen:
        raise ValueError('MThd 头被截断')
    header = {
        'format': int.from_bytes(data[8:10], 'big'),
        'ntrks': int.from_bytes(data[10:12], 'big'),
        'division': int.from_bytes(data[12:14], 'big'),
    }
    tracks: List[List[Event]] = []
    pos = 8 + hlen
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        clen = int.from_bytes(data[pos + 4:pos + 8], 'big')
        payload = data[pos + 8:pos + 8 + clen]  # 截断时自动取到末尾
        if cid == b'MTrk':
            tracks.append(parse_track(payload, track=len(tracks)))
        pos += 8 + clen
    return header, tracks


def merge_smf(data: bytes) -> List[Event]:
    """解析 SMF 并直接返回合并后的事件流。"""
    _, tracks = parse_smf(data)
    return merge_tracks(tracks)
