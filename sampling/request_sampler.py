"""请求级采样：保证同一请求的日志要么全留、要么全丢。

两种模式：

BufferedRequestSampler（推荐，默认）
    事件先进缓冲区，end_request() 时基于该请求“最严重”的事件做一次
    尾部决策（tail sampling）。天然全留/全丢；请求内出现 ERROR/FATAL
    或受保护通道/路径时强制保留 —— 错误日志与关键路径日志绝不丢。
    决策基于请求开始时锁定的规则快照，跨规则更新也保持一致。

HeadRequestSampler（低内存流式场景）
    首个事件到达时立即决策并缓存，同请求后续事件复用该决策。
    后续若出现“强制保留”事件（错误/受保护），决策升级为保留，
    此前已丢的该请求事件会被补记到 shadow 缓冲并在升级时放出，
    因此对外仍满足全留/全丢。内存占用为 O(活跃请求数)。
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .layered_sampler import (
    Event,
    LayeredSampler,
    Match,
    _Snapshot,
    assert_request_consistency,
)


@dataclass
class FlushResult:
    """一次请求收尾的结果。"""

    request_id: str
    kept: bool
    events: List[Event]
    match: Match
    rule_version: int
    upgraded: bool = False  # head 模式下是否发生了“错误升级”


class BufferedRequestSampler:
    """尾部决策的请求级采样器（同一请求全留/全丢，严格保证）。"""

    def __init__(
        self,
        sampler: LayeredSampler,
        rng: Optional[Callable[[], float]] = None,
        max_buffered_events: int = 100_000,
    ) -> None:
        self._sampler = sampler
        self._rng = rng or __import__("random").Random(0).random
        self._max_buffered = max_buffered_events
        self._lock = threading.RLock()
        # request_id -> (快照, 事件缓冲)
        self._buffers: "OrderedDict[str, Tuple[_Snapshot, List[Event]]]" = (
            OrderedDict()
        )
        self._pending_events = 0

    @property
    def pending_requests(self) -> int:
        return len(self._buffers)

    def record(self, event: Event) -> None:
        """缓冲一条事件；首次见到该请求时锁定当前规则快照。"""
        with self._lock:
            entry = self._buffers.get(event.request_id)
            if entry is None:
                entry = (self._sampler.snapshot, [])
                self._buffers[event.request_id] = entry
            if self._pending_events >= self._max_buffered:
                raise RuntimeError(
                    "缓冲事件数超过上限，请先 end_request() 释放内存"
                )
            entry[1].append(event)
            self._pending_events += 1

    def end_request(self, request_id: str) -> FlushResult:
        """请求结束：做一次统一决策，返回全留或全丢的事件列表。"""
        with self._lock:
            entry = self._buffers.pop(request_id, None)
            if entry is None:
                raise KeyError(f"未知或已结束的请求: {request_id!r}")
            snapshot, events = entry
            self._pending_events -= len(events)

        # 尾部决策：以请求内“最严格”的事件做匹配 —— 任一事件命中
        # 强制保留（错误级别/受保护通道/keep 规则）则整个请求保留。
        # 治理事件选择：强制保留(错误/关键路径) > 最高严重级别 >
        # 更具体规则(优先级数字小) > 更高保留率。
        governing: Optional[Match] = None
        governing_ev: Optional[Event] = None
        for ev in events:
            m = self._sampler.match(ev, snapshot=snapshot)
            if m.forced_keep:
                governing, governing_ev = m, ev
                break
            if (
                governing is None
                or ev.level > governing_ev.level
                or (
                    ev.level == governing_ev.level
                    and (
                        m.priority < governing.priority
                        or (
                            m.priority == governing.priority
                            and m.rate > governing.rate
                        )
                    )
                )
            ):
                governing, governing_ev = m, ev
        assert governing is not None  # events 非空（record 至少一条）

        if governing.rate in (0.0, 1.0):
            kept = governing.rate == 1.0
        else:
            kept = self._rng() < governing.rate

        # 请求级一致性断言：同一请求内所有事件决策必须一致。
        assert_request_consistency(request_id, [kept] * len(events))

        return FlushResult(
            request_id=request_id,
            kept=kept,
            events=events if kept else [],
            match=governing,
            rule_version=snapshot.version,
        )

    def flush_all(self) -> List[FlushResult]:
        """收尾所有未结束请求（进程退出前调用）。"""
        results = []
        while True:
            with self._lock:
                if not self._buffers:
                    return results
                request_id = next(iter(self._buffers))
            results.append(self.end_request(request_id))


class HeadRequestSampler:
    """流式 head 决策：首个事件决策并缓存，后续错误可升级决策。"""

    def __init__(
        self,
        sampler: LayeredSampler,
        rng: Optional[Callable[[], float]] = None,
        max_tracked_requests: int = 10_000,
    ) -> None:
        self._sampler = sampler
        self._rng = rng or __import__("random").Random(0).random
        self._max_tracked = max_tracked_requests
        self._lock = threading.RLock()
        # request_id -> (快照, kept, shadow缓冲)
        self._decisions: "OrderedDict[str, Tuple[_Snapshot, bool, List[Event]]]" = (
            OrderedDict()
        )

    @property
    def tracked_requests(self) -> int:
        return len(self._decisions)

    def should_emit(self, event: Event) -> bool:
        """返回该事件是否应立即输出。"""
        with self._lock:
            entry = self._decisions.get(event.request_id)
            if entry is None:
                if len(self._decisions) >= self._max_tracked:
                    # 淘汰最旧的进行中请求，避免内存膨胀。
                    self._decisions.popitem(last=False)
                snapshot = self._sampler.snapshot
                m = self._sampler.match(event, snapshot=snapshot)
                kept = (
                    m.rate == 1.0
                    if m.rate in (0.0, 1.0)
                    else self._rng() < m.rate
                )
                shadow = [] if kept else [event]  # 首事件若被丢也要影子缓冲
                self._decisions[event.request_id] = (snapshot, kept, shadow)
                return kept

            snapshot, kept, shadow = entry
            m = self._sampler.match(event, snapshot=snapshot)
            if not kept and m.forced_keep:
                # 错误/关键路径迟到：升级整个请求为保留，
                # 影子缓冲保留，end_request 时补放。
                self._decisions[event.request_id] = (snapshot, True, shadow)
                return True
            if not kept:
                # 已判丢弃的请求：影子缓冲，供升级时补放。
                shadow.append(event)
                return False
            return True

    def end_request(self, request_id: str) -> Optional[List[Event]]:
        """请求结束：返回升级后需要补放的影子事件（无升级则为空列表）。"""
        with self._lock:
            entry = self._decisions.pop(request_id, None)
        if entry is None:
            return None
        _, kept, shadow = entry
        return shadow if kept else []
