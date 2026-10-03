"""变更流分发与顺序保证库（仅标准库）。

核心模型：
- Change        一次行变更，带全局单调位点 lsn（log sequence number）
- ChangeLog     追加式变更日志（JSONL 落盘），重启后可重读
- CheckpointStore 消费者位点（ack 位点）持久化，落盘 JSON
- Dispatcher    按主键哈希分区：同一主键恒定落到同一分区，分区内严格按 lsn 投递，
                 不同分区（从而不同主键）可由多个 worker 线程并行处理
- ReorderBuffer 将并行处理后乱序到达的结果按全局 lsn 重新排序

投递语义：at-least-once。消费者在“处理成功 -> 提交位点”之间崩溃时，
重启后会从上次已提交位点 + 1 重放，因此同一变更可能被重复投递；
业务侧用幂等键（lsn）去重即可，见 test_cdc.py。
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
import time
from dataclasses import dataclass, asdict
from typing import Callable, Dict, Iterable, Iterator, List, Optional


@dataclass(frozen=True)
class Change:
    lsn: int
    pk: str
    op: str           # INSERT / UPDATE / DELETE
    payload: dict

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    @staticmethod
    def from_json(line: str) -> "Change":
        d = json.loads(line)
        return Change(lsn=int(d["lsn"]), pk=d["pk"], op=d["op"], payload=d["payload"])


class ChangeLog:
    """追加式变更日志。lsn 从 1 开始全局单调递增。"""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        if os.path.exists(path):
            self._next_lsn = 1 + max((c.lsn for c in self._scan()), default=0)
        else:
            self._next_lsn = 1
            open(path, "a").close()

    def append(self, pk: str, op: str, payload: Optional[dict] = None) -> Change:
        with self._lock:
            change = Change(lsn=self._next_lsn, pk=pk, op=op, payload=payload or {})
            self._next_lsn += 1
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(change.to_json() + "\n")
                f.flush()
                os.fsync(f.fileno())
        return change

    def _scan(self) -> Iterator[Change]:
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield Change.from_json(line)

    def read_from(self, acked_lsn: int) -> List[Change]:
        """返回所有 lsn > acked_lsn 的变更（保持日志顺序）。"""
        return [c for c in self._scan() if c.lsn > acked_lsn]

    def all(self) -> List[Change]:
        return list(self._scan())


class CheckpointStore:
    """消费者位点持久化：{consumer_id: 已确认 lsn}。"""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._offsets: Dict[str, int] = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read().strip()
                if raw:
                    self._offsets = {k: int(v) for k, v in json.loads(raw).items()}

    def get(self, consumer_id: str) -> int:
        with self._lock:
            return self._offsets.get(consumer_id, 0)

    def commit(self, consumer_id: str, lsn: int) -> None:
        """提交位点；位点只进不退。"""
        with self._lock:
            if lsn < self._offsets.get(consumer_id, 0):
                raise ValueError(f"位点不能回退: {lsn} < {self._offsets[consumer_id]}")
            self._offsets[consumer_id] = lsn
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._offsets, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._offsets)


class Dispatcher:
    """按主键把变更哈希到固定数量的分区。

    分区数不变时，同一主键永远进同一分区；每个分区单 worker 顺序消费，
    因此同一主键严格按产生顺序（lsn 升序）投递。不同分区由不同线程并行处理。
    """

    def __init__(self, num_partitions: int):
        if num_partitions < 1:
            raise ValueError("至少需要 1 个分区")
        self.num_partitions = num_partitions

    def partition_for(self, pk: str) -> int:
        digest = hashlib.blake2b(pk.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") % self.num_partitions

    def split(self, changes: Iterable[Change]) -> List[List[Change]]:
        buckets: List[List[Change]] = [[] for _ in range(self.num_partitions)]
        for c in changes:
            buckets[self.partition_for(c.pk)].append(c)
        return buckets


Handler = Callable[[Change], None]


class _SimulatedCrash(Exception):
    """用于测试：模拟消费者在处理中途崩溃（位点尚未提交）。"""


def _cp_key(consumer_id: str, partition: int) -> str:
    # 位点按“消费者组 + 分区”记录；不同分区独立提交，互不拖累
    return f"{consumer_id}:p{partition}"


class ParallelConsumer:
    """带位点恢复的并行消费者（消费者组）。

    一次 process_batch()：
    1. 每个分区从 CheckpointStore 读自己上次已提交位点；
    2. 从日志重放该位点之后的变更，只保留属于本分区的（过滤后仍保持 lsn 顺序）；
    3. 各分区由独立 worker 线程并行处理，分区内严格按 lsn 调用 handler 并逐条提交位点。
    进程崩溃后重新构造本对象即完成“重启”，未确认的变更会被重新投递。
    位点按分区独立记录：分区 A 的高位点提交不会跳过分区 B 未确认的低位点。
    """

    def __init__(self, consumer_id: str, log: ChangeLog, checkpoints: CheckpointStore,
                 dispatcher: Dispatcher):
        self.consumer_id = consumer_id
        self.log = log
        self.checkpoints = checkpoints
        self.dispatcher = dispatcher

    def process_batch(self, handler: Handler,
                      crash_after: Optional[Callable[[Change], bool]] = None) -> List[Change]:
        """处理一批未确认变更，返回实际投递（含重复投递）的变更，按完成顺序。

        crash_after: 若给定且对某变更返回 True，则在“该变更已处理、位点尚未提交”时
                     模拟崩溃：该 worker 立即停止；其余分区跑完；随后向调用方抛出
                     _SimulatedCrash。重启（重新构造消费者）后该分区从断点重放。
        """
        buckets = self.dispatcher.split(self.log.all())
        delivered: List[Change] = []
        delivered_lock = threading.Lock()
        failures: List[BaseException] = []
        barrier: "queue.Queue[None]" = queue.Queue()

        def worker(partition: int, bucket: List[Change]) -> None:
            key = _cp_key(self.consumer_id, partition)
            acked = self.checkpoints.get(key)
            try:
                for change in bucket:  # bucket 保持日志顺序 => 同主键严格有序
                    if change.lsn <= acked:
                        continue  # 本分区已确认，跳过（其他分区的位点与此无关）
                    handler(change)
                    with delivered_lock:
                        delivered.append(change)
                    if crash_after is not None and crash_after(change):
                        raise _SimulatedCrash(change)  # 故意不提交位点
                    self.checkpoints.commit(key, change.lsn)
            except BaseException as exc:  # noqa: BLE001 - 汇总到主线程
                failures.append(exc)
            finally:
                barrier.put(None)

        threads = []
        for partition, bucket in enumerate(buckets):
            if not bucket:
                continue
            t = threading.Thread(target=worker, args=(partition, bucket), daemon=True)
            t.start()
            threads.append(t)
        for _ in threads:
            barrier.get()
            time.sleep(0)  # 让并行交错更真实
        for t in threads:
            t.join(timeout=5)

        crash = next((e for e in failures if isinstance(e, _SimulatedCrash)), None)
        other = next((e for e in failures if not isinstance(e, _SimulatedCrash)), None)
        if other is not None:
            raise other
        if crash is not None:
            raise crash
        return delivered

    def committed_offsets(self) -> Dict[int, int]:
        """返回本消费者各分区已提交位点（位点记录样例用）。"""
        return {
            p: self.checkpoints.get(_cp_key(self.consumer_id, p))
            for p in range(self.dispatcher.num_partitions)
        }


class ReorderBuffer:
    """把并行处理后乱序到达的变更按全局 lsn 重排。

    用法：不断 add()，再 drain()；drain 只吐出从 expected_lsn 起连续无缺口的前缀，
    缺口之后的变更缓存在内存中，等对应位点到达后再输出。
    """

    def __init__(self, expected_lsn: int = 1):
        self._expected = expected_lsn
        self._pending: Dict[int, Change] = {}

    def add(self, change: Change) -> None:
        if change.lsn < self._expected:
            return  # 已输出过（重复投递），忽略
        if change.lsn in self._pending:
            return  # 重复
        self._pending[change.lsn] = change

    def drain(self) -> List[Change]:
        out: List[Change] = []
        while self._expected in self._pending:
            out.append(self._pending.pop(self._expected))
            self._expected += 1
        return out

    @property
    def expected_lsn(self) -> int:
        return self._expected
