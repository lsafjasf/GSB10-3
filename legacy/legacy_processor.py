"""
重构前：一个「事件批次提交器」。

下面这些约定只存在于注释和老员工脑子里，新人一改就坏，测试也抓不到：

- event 必须恰好有这 8 个 key，少一个多一个都不行。
- type == 'trade' 的事件必须带 symbol；quote 事件 symbol 必须为 None。
- amount 是 int（不是 float！），范围 [0, 1_000_000_000]。
- occurred_at 是 int 且为非负（Unix 毫秒）。
- tags 是 tuple[str, ...]，0..3 个，不能重复，元素必须 hashable
  （别塞 list 进来，去重会炸）。
- symbol 只能是 'AAPL' / 'GOOG' / 'TSLA'。
- 一个批次最多 1000 个事件。
- 生命周期必须是 open -> add_event* -> (close) -> commit -> done，
  不能跳步，commit 后不能再加事件。
- 同一批次内事件必须按 occurred_at 非递减。
- on_commit 回调必须在 commit 之前注册，commit 之后不允许注册。
- 本对象只能在创建它的线程里使用（非线程安全）。
- _snapshot_locked/_advance_locked 是内部方法，只能在持锁状态下调用。
- commit 释放锁之后才能调外部 sink（防止回调反向调用造成死锁）。
- commit 返回的 receipt 必须含固定 5 个字段。
"""
import threading

MAX_BATCH = 1000
ALLOWED_SYMBOLS = ("AAPL", "GOOG", "TSLA")


class BatchProcessor:
    def __init__(self, sink):
        self.sink = sink
        self.events = []
        self.state = "open"          # open -> closed -> done
        self.on_commit = None
        self.lock = threading.RLock()

    def set_on_commit(self, cb):
        self.on_commit = cb

    def add_event(self, event):
        # 没人检查 event 长什么样、state 对不对、顺序乱没乱、超没超 1000。
        self.events.append(event)

    def close(self):
        self.state = "closed"

    def commit(self):
        total = sum(e["amount"] for e in self.events)
        receipt = {
            "batch_id": id(self),
            "event_ids": [e["event_id"] for e in self.events],
            "count": len(self.events),
            "total_amount": total,
            "committed_at": None,
        }
        # 注释说：此时必须已经不持锁，否则回调里调我们会死锁。但没人拦着。
        self.sink.publish(receipt)
        if self.on_commit:
            self.on_commit(receipt)
        self.state = "done"
        return receipt
