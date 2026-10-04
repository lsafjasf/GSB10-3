"""分层定时器轮（Hierarchical Timing Wheel）。

特性：
- 仅依赖 Python 标准库；
- 逻辑时间不取自系统时钟，完全由调用方通过 ``start_time`` / ``advance(now)`` 注入；
- 插入 O(层数)，取消 O(1)：取消为懒惰取消，只在节点上打标记，
  节点仍留在桶中，直到推进到该桶时被跳过并回收；
- 超时超过一层覆盖范围时自动向高层溢出建层，时间推进时逐层向低层降级；
- 同一绝对时刻（相同 deadline）的触发顺序严格等于插入顺序
  （每个桶是 FIFO，相同 deadline 在任何一层都映射到同一个桶，
  降级过程中相对顺序不变）。

时间量化：deadline 按 ``base_interval`` 量化到所属 tick，tick 内的任务
在该 tick 起点被处理（最多提前 ``base_interval - 1`` 个时间单位）；
``base_interval=1`` 时语义精确。
"""

from bisect import insort
from collections import deque
from itertools import count


def _order_key(node):
    # 桶内全局有序：先按到期时间，再按插入序号。
    # 这样无论节点经哪条路径（直接插入 / 高层降级）进入桶，
    # 相同 deadline 的触发顺序都严格等于插入顺序。
    return (node.deadline, node.seq)


class TimerHandle:
    """``insert`` 返回的任务句柄。"""

    __slots__ = ("deadline", "seq", "payload", "cancelled", "fired", "_wheel")

    def __init__(self, wheel, deadline, seq, payload):
        self._wheel = wheel
        self.deadline = deadline  # 绝对到期时间（逻辑时间）
        self.seq = seq            # 全局插入序号，越小越早插入
        self.payload = payload
        self.cancelled = False
        self.fired = False

    def cancel(self):
        """懒惰取消：只打标记并立即从待处理计数中移除，不从桶里摘除节点。

        返回 True 表示本次调用成功取消；重复取消、或任务已触发则返回 False。
        被取消的节点保证永远不会被触发。
        """
        if self.cancelled or self.fired:
            return False
        self.cancelled = True
        self._wheel._size -= 1
        return True

    @property
    def pending(self):
        return not self.cancelled and not self.fired

    def __repr__(self):
        return (
            "TimerHandle(deadline=%r, seq=%r, payload=%r, cancelled=%r)"
            % (self.deadline, self.seq, self.payload, self.cancelled)
        )


class _Layer:
    """单层时间轮：interval 为 tick 长度，buckets 数量固定，span=interval*slots。"""

    __slots__ = ("interval", "current_time", "buckets")

    def __init__(self, interval, current_time, slots):
        self.interval = interval
        # current_time 始终是 interval 的整数倍，且为指针当前所指 tick 的起点
        self.current_time = current_time
        self.buckets = [[] for _ in range(slots)]

    @property
    def span(self):
        return self.interval * len(self.buckets)

    def index(self, time_value):
        return (time_value // self.interval) % len(self.buckets)


class HierarchicalTimingWheel:
    """分层定时器轮。

    参数：
        base_interval:   最底层（第 0 层）的 tick 长度，必须为正。
        slots_per_layer: 每层槽位数（至少 2）；第 i 层 interval 为
                         base_interval * slots_per_layer ** i。
        start_time:      注入的初始逻辑时间。
    """

    def __init__(self, base_interval=1, slots_per_layer=64, start_time=0):
        if base_interval <= 0:
            raise ValueError("base_interval must be > 0")
        if slots_per_layer < 2:
            raise ValueError("slots_per_layer must be >= 2")
        self.base_interval = base_interval
        self.slots_per_layer = slots_per_layer
        self.now = start_time
        aligned_start = start_time - (start_time % base_interval)
        self._layers = [_Layer(base_interval, aligned_start, slots_per_layer)]
        self._seq = count()
        self._size = 0
        # 插入时即已落在当前 tick（立即超时）的节点，统一在下一次 advance 交付
        self._due_now = deque()

    def __len__(self):
        """当前存活（未取消、未触发）的任务数。"""
        return self._size

    @property
    def levels(self):
        """当前层数（会因超大超时而自动增长）。"""
        return len(self._layers)

    def insert(self, delay, payload=None):
        """插入一个相对超时为 delay 的任务，返回 TimerHandle。"""
        if delay < 0:
            raise ValueError("delay must be >= 0")
        node = TimerHandle(self, self.now + delay, next(self._seq), payload)
        self._size += 1
        if not self._offer(node):
            self._due_now.append(node)
        return node

    def cancel(self, node):
        return node.cancel()

    def advance(self, now):
        """把逻辑时间推进到 now（只能单调向前），返回本次触发的节点列表。

        返回顺序即真实触发顺序：deadline 非降；相同 deadline 严格按插入顺序。
        """
        if now < self.now:
            raise ValueError("time cannot move backwards")
        fired = []
        # 自高层向低层推进：高层跨过边界时，递归先把低层推到该边界，
        # 然后把桶里的节点重新 offer，实现逐层降级（或直接触发）。
        for level in range(len(self._layers) - 1, -1, -1):
            self._advance_layer(level, now, fired)
        # 立即到期队列与本次从桶中触发的节点统一按 (deadline, seq) 归并：
        # 立即到期节点可能在上一次 advance 之后插入（deadline 更早），
        # 不能简单地排在队尾，否则会破坏全局触发顺序。
        while self._due_now:
            node = self._due_now.popleft()
            if not node.cancelled:
                self._fire(node, fired)
        fired.sort(key=_order_key)
        self.now = now
        return fired

    def _advance_layer(self, level, now, fired):
        layer = self._layers[level]
        while layer.current_time + layer.interval <= now:
            if not any(layer.buckets):
                # 整层没有任务：直接跳到对齐位置，避免逐 tick 空转，
                # 从而即使一次 advance 跨越极长时间也不会线性变慢。
                # 跳转前必须先把低层推到目标时刻，否则低层未处理的任务
                # 会晚于本层后续降级下来的任务触发，破坏顺序不变式。
                if level > 0:
                    self._advance_layer(level - 1, now, fired)
                layer.current_time = now - (now % layer.interval)
                break
            boundary = layer.current_time + layer.interval
            if level > 0:
                # 高层指针跨过该边界前，低层必须已推进到同一时刻
                self._advance_layer(level - 1, boundary, fired)
            layer.current_time = boundary
            bucket = layer.buckets[layer.index(boundary)]
            for node in bucket:
                if node.cancelled:
                    continue  # 懒惰取消：节点在此被回收，永不触发
                if not self._offer(node):
                    self._fire(node, fired)
            bucket.clear()

    def _offer(self, node):
        """把节点放入合适的层/桶。返回 False 表示已经到期，应当触发。"""
        for layer in self._layers:
            if node.deadline < layer.current_time + layer.interval:
                # 落在该层当前 tick 内：要么更低层能接纳，要么已经到期。
                # 层按从低到高遍历，到达这里意味着最低层也认为已到期。
                return False
            if node.deadline < layer.current_time + layer.span:
                insort(layer.buckets[layer.index(node.deadline)], node, key=_order_key)
                return True
        # 超出最高层覆盖范围：自动长出新的一层后再试。
        self._grow()
        return self._offer(node)

    def _grow(self):
        top = self._layers[-1]
        interval = top.interval * self.slots_per_layer
        current_time = top.current_time - (top.current_time % interval)
        self._layers.append(_Layer(interval, current_time, self.slots_per_layer))

    def _fire(self, node, fired):
        node.fired = True
        self._size -= 1
        fired.append(node)
