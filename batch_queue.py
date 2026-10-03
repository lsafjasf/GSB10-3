"""有界阻塞队列：支持批量拉取、关闭语义，空队列等待不忙等。

仅使用标准库（threading + collections）。

实现要点：一把锁保护两个条件变量
- not_empty: 消费者在队列为空时挂起，由生产者 put 唤醒；
- not_full:  生产者在队列满时挂起，由消费者取走元素后唤醒。
通知精确发往对侧，消费者永远不会因为"腾空间"而被误唤醒，
因此空队列等待期间不存在任何忙等轮询。
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any, Deque, List, Optional


class QueueClosed(Exception):
    """向已关闭的队列 put 时抛出。"""


class BoundedBatchQueue:
    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._items: Deque[Any] = deque()
        self._closed = False
        self._lock = threading.RLock()
        self._not_empty = threading.Condition(self._lock)
        self._not_full = threading.Condition(self._lock)
        # 空转次数：消费者被唤醒却发现队列仍为空、只能继续挂起的次数。
        # 双条件精确通知下该值恒为 0；退化成忙等时该值会迅速增长。
        self.spin_count = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def close(self) -> None:
        """关闭队列：不再接受 put，唤醒全部等待者；已入队元素仍可取完。"""
        with self._lock:
            self._closed = True
            self._not_empty.notify_all()
            self._not_full.notify_all()

    def put(self, item: Any, timeout: Optional[float] = None) -> None:
        """入队一个元素；队列满时在 not_full 上阻塞等待空位。"""
        with self._lock:
            while len(self._items) >= self._capacity:
                if self._closed:
                    raise QueueClosed("put on a closed queue")
                if not self._not_full.wait(timeout):
                    raise TimeoutError("put timed out: queue is full")
            if self._closed:
                raise QueueClosed("put on a closed queue")
            self._items.append(item)
            self._not_empty.notify()  # 每放入一个，唤醒一个消费者

    def pull_batch(
        self,
        limit: int,
        remaining: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> List[Any]:
        """批量拉取。

        实际拉取数量 = min(limit, remaining, 当前已入队数量)。
        - limit: 批量上限（必须为正）。
        - remaining: 消费方缓冲的剩余容量；缺省视为与 limit 相同。
        - 队列为空且未关闭时在 not_empty 上挂起等待；超时抛 TimeoutError。
        - 返回 [] 表示结束信号：队列已关闭且没有剩余元素。
        """
        if limit <= 0:
            raise ValueError("limit must be positive")
        if remaining is None:
            remaining = limit
        if remaining <= 0:
            raise ValueError("remaining must be positive")
        wanted = min(limit, remaining)

        with self._lock:
            # 空队列：挂起等待，绝不轮询。
            while not self._items:
                if self._closed:
                    return []  # 已关闭且取完：结束信号
                if not self._not_empty.wait(timeout):
                    raise TimeoutError("pull_batch timed out: queue is empty")
                if not self._items and not self._closed:
                    # 理论上双条件精确通知不会走到这里；
                    # 若被误唤醒而无数据，计一次空转（防忙等回归）。
                    self.spin_count += 1

            if not self._items:
                return []

            n = min(wanted, len(self._items))
            batch = [self._items.popleft() for _ in range(n)]
            for _ in range(n):
                self._not_full.notify()  # 每取走一个，唤醒一个生产者
            return batch
