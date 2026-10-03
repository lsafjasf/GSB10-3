"""有界队列 + 批量拉取（仅标准库）。

核心点：
- 空队列时消费者在 Condition 上阻塞等待，由生产者 / close() 唤醒，
  不存在无阻塞的重检循环（忙等）。库内置空转计数，便于自测断言。
- 单次拉取数量 = min(批量上限 max_items, 接收缓冲剩余容量 free_space,
  队列中已入队元素数)。
- close() 之后：已入队元素可以继续被取完；取完后（以及当时的等待者）
  收到结束信号 QUEUE_CLOSED。
"""

from __future__ import annotations

import threading
from collections import deque
from queue import Empty
from typing import Any, List, Optional

# 结束信号：队列已关闭且元素已取干。用单例对象做同一性比较，
# 不与任何业务元素冲突（即使业务元素本身是 None）。
QUEUE_CLOSED = object()


class QueueClosed(Exception):
    """队列关闭后继续 put 时抛出。"""


class BoundedBatchQueue:
    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        self._capacity = capacity
        self._items: deque[Any] = deque()
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._not_full = threading.Condition(self._lock)
        self._closed = False

        # 空转诊断：
        # _empty_checks —— 持锁后发现“空且未关闭”的次数；
        # _wait_calls   —— 实际调用 Condition.wait() 阻塞的次数。
        # 正确实现里每次空检之后必然紧跟一次 wait，二者恒等；
        # 忙等实现会让 _empty_checks 暴涨而 _wait_calls 为 0。
        self._empty_checks = 0
        self._wait_calls = 0

    def capacity(self) -> int:
        return self._capacity

    def size(self) -> int:
        with self._lock:
            return len(self._items)

    def is_closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def busy_spins(self) -> int:
        """非阻塞空转次数。消费者处于阻塞中或已返回时必须为 0。"""
        with self._lock:
            return self._empty_checks - self._wait_calls

    def put(self, item: Any, timeout: Optional[float] = None) -> None:
        """入队一个元素；队列满时阻塞，直到有空位或队列被关闭。"""
        with self._not_full:
            if self._closed:
                raise QueueClosed("queue is closed")
            if len(self._items) >= self._capacity:
                if timeout is None:
                    while len(self._items) >= self._capacity and not self._closed:
                        self._not_full.wait()
                else:
                    if not self._not_full.wait_for(
                        lambda: len(self._items) < self._capacity or self._closed,
                        timeout,
                    ):
                        raise Empty("put timed out")
            if self._closed:
                raise QueueClosed("queue is closed")
            self._items.append(item)
            self._not_empty.notify()

    def pull_batch(
        self,
        max_items: int,
        free_space: int,
        timeout: Optional[float] = None,
    ) -> Any:
        """批量拉取。

        返回值：
        - 有元素时返回长度 >= 1 的列表，条数为
          min(max_items, free_space, 队列已有条数)；
        - 队列已关闭且已取干时返回 QUEUE_CLOSED；
        - timeout 到期仍没有元素时抛 queue.Empty。
        """
        if max_items <= 0:
            raise ValueError("max_items must be positive")
        if free_space <= 0:
            raise ValueError("free_space must be positive")

        with self._not_empty:
            while True:
                available = len(self._items)
                if available > 0:
                    take = min(max_items, free_space, available)
                    batch: List[Any] = [
                        self._items.popleft() for _ in range(take)
                    ]
                    self._not_full.notify(take)
                    return batch

                if self._closed:
                    return QUEUE_CLOSED

                # 空且未关闭：计数后立即在条件变量上阻塞，
                # 绝不在未阻塞的情况下绕回重检（那是忙等）。
                # 注意 _wait_calls 在 wait 之前递增：两个计数都在持锁状态下
                # 修改，外部观察者永远只能看到“相等”或“刚阻塞”两种状态。
                self._empty_checks += 1
                self._wait_calls += 1
                if timeout is None:
                    self._not_empty.wait()
                else:
                    got = self._not_empty.wait_for(
                        lambda: bool(self._items) or self._closed, timeout
                    )
                    if not got:
                        raise Empty("pull_batch timed out")

    def close(self) -> None:
        """关闭队列：唤醒所有等待者，不再接受新元素；存量元素可继续取。"""
        with self._lock:
            self._closed = True
            self._not_empty.notify_all()
            self._not_full.notify_all()
