"""批次提交器：调用顺序 + 线程约束 + 回执形状类约定的可执行断言。"""

import threading

from .contracts import check
from .events import validate_event

STATE_OPEN = "open"
STATE_CLOSED = "closed"
STATE_DONE = "done"
MAX_BATCH = 1000

RECEIPT_KEYS = frozenset(("batch_id", "event_ids", "seq", "total_amount", "committed_at"))


def _validate_receipt(receipt):
    # [S3] receipt 必须恰好含固定 5 个字段，类型固定。
    check(isinstance(receipt, dict)
          and frozenset(receipt.keys()) == RECEIPT_KEYS
          and isinstance(receipt["batch_id"], str)
          and isinstance(receipt["event_ids"], tuple)
          and all(isinstance(value, str) for value in receipt["event_ids"])
          and type(receipt["seq"]) is int
          and type(receipt["total_amount"]) is int
          and type(receipt["committed_at"]) is int, "S3",
          f"receipt 形状非法：{receipt!r}")


class BatchProcessor:
    def __init__(self, sink, batch_id, max_batch=MAX_BATCH):
        check(hasattr(sink, "publish") and callable(getattr(sink, "publish")),
              "S0", "sink 必须提供可调用的 publish 方法")
        check(type(max_batch) is int and max_batch > 0, "S0",
              f"max_batch 必须是正 int，实际 {max_batch!r}")
        self._sink = sink
        self._batch_id = batch_id
        self._max_batch = max_batch
        self._events = []
        self._state = STATE_OPEN
        self._on_commit = None
        self._lock = threading.RLock()
        self._owner = threading.get_ident()

    # -- 线程约束 ----------------------------------------------------------

    def _require_owner(self):
        # [T1] 实例只能在创建它的线程里调用公共方法。
        check(threading.get_ident() == self._owner, "T1",
              f"BatchProcessor 绑定线程 {self._owner}，当前线程 {threading.get_ident()}")

    # -- 生命周期 ----------------------------------------------------------

    def _require_state(self, allowed, action):
        check(self._state in allowed, "O1",
              f"不能在 state={self._state!r} 时执行 {action}，仅允许 {sorted(allowed)}")

    def set_on_commit(self, callback):
        """[O3] 回调只能在 commit 之前注册。"""
        self._require_owner()
        check(callable(callback), "O3", "on_commit 回调必须可调用")
        check(self._state != STATE_DONE, "O3", "commit 完成后不允许再注册 on_commit")
        self._on_commit = callback

    def close(self):
        self._require_owner()
        self._require_state((STATE_OPEN,), "close()")
        self._state = STATE_CLOSED

    def add_event(self, event):
        self._require_owner()
        self._require_state((STATE_OPEN,), "add_event()")
        validate_event(event)
        # [O2] 同一批次内事件必须按 occurred_at 非递减。
        previous = self._events[-1]["occurred_at"] if self._events else None
        check(not self._events or event["occurred_at"] >= previous,
              "O2",
              f"occurred_at 必须非递减：上一个 {previous}，"
              f"当前 {event['occurred_at']}")
        # [R7] 单批次事件数上限。
        check(len(self._events) < self._max_batch, "R7",
              f"单批次最多 {self._max_batch} 个事件")
        self._events.append(event)

    def commit(self):
        self._require_owner()
        self._require_state((STATE_CLOSED,), "commit()（必须先 close）")
        with self._lock:
            receipt = self._snapshot_locked()
            self._advance_locked()
        self._publish(receipt)
        return receipt

    # -- 内部方法：只能在持锁状态下调用 ------------------------------------

    def _snapshot_locked(self):
        # [T3] *_locked 内部方法只能在持锁时调用。
        check(self._lock._is_owned(), "T3", "_snapshot_locked 必须在持锁状态下调用")
        return {
            "batch_id": self._batch_id,
            "event_ids": tuple(event["event_id"] for event in self._events),
            "seq": len(self._events),
            "total_amount": sum(event["amount"] for event in self._events),
            "committed_at": 0,
        }

    def _advance_locked(self):
        # [T3] *_locked 内部方法只能在持锁时调用。
        check(self._lock._is_owned(), "T3", "_advance_locked 必须在持锁状态下调用")
        self._state = STATE_DONE

    def _publish(self, receipt):
        # [T2] 外部调用（sink / 回调）必须在锁外进行，防止重入死锁。
        check(not self._lock._is_owned(), "T2", "外部调用（sink/回调）不允许在持锁状态下进行")
        _validate_receipt(receipt)
        self._sink.publish(receipt)
        if self._on_commit is not None:
            self._on_commit(receipt)
