"""
复制日志的拉取与续传（仅依赖 Python 3 标准库）。

设计要点
========
* 位点语义：checkpoint 中保存的是 ``next_lsn``，即“下一条期望拉取的日志序号”。
  每一批成功 apply 之后才按批次原子落盘（tmp + rename + fsync），
  因此重启后最多重复投递一批（at-least-once），由消费端去重保证幂等。
* 缺口检测：源端清理了请求位点之前的日志时，抛出 ``PositionPurged``，
  拉取器据此产出 ``GapReport``，显式报告缺口闭区间
  ``[requested_lsn, earliest_available - 1]``，绝不静默跳到可用位置。
* 超时与重试：每次 fetch 带超时；``TimeoutError`` / 连接类异常按
  指数退避重试，超过最大次数向上抛出，期间不推进位点。
* 批次去重：消费端 ``SequenceSink`` 按 lsn 幂等 apply，是跨重启去重的
  持久依据；重复投递的批次/记录会被跳过并计数
  （``duplicate_batches`` / ``duplicate_records``）。

LSN 从 0 开始连续递增；checkpoint 文件为单行 JSON。
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Protocol, Set, Tuple


# --------------------------------------------------------------------------- #
# 异常与报告结构
# --------------------------------------------------------------------------- #


class PositionPurged(Exception):
    """请求的位点对应的日志已被源端清理（再无法补拉）。"""

    def __init__(self, requested_lsn: int, earliest_available: int):
        self.requested_lsn = requested_lsn
        self.earliest_available = earliest_available
        super().__init__(
            "position %d has been purged; earliest available is %d"
            % (requested_lsn, earliest_available)
        )


class FetchTimeout(Exception):
    """拉取在指定超时时间内没有返回。"""


class FetchUnavailable(Exception):
    """网络中断等临时性不可用，重试后可能恢复。"""


class UnrecoverableFetchError(Exception):
    """重试耗尽后仍失败。"""


@dataclass(frozen=True)
class GapReport:
    """位点被清理时的缺口报告：缺口为闭区间 [gap_start, gap_end]。"""

    requested_lsn: int
    earliest_available: int

    @property
    def gap_start(self) -> int:
        return self.requested_lsn

    @property
    def gap_end(self) -> int:
        return self.earliest_available - 1

    @property
    def gap_size(self) -> int:
        return self.earliest_available - self.requested_lsn

    def to_dict(self) -> dict:
        return {
            "type": "log_gap",
            "requested_lsn": self.requested_lsn,
            "earliest_available": self.earliest_available,
            "gap_interval": [self.gap_start, self.gap_end],
            "gap_size": self.gap_size,
            "message": (
                "log records in [%d, %d] have been purged and cannot be "
                "replicated; consumer will NOT skip ahead automatically"
                % (self.gap_start, self.gap_end)
            ),
        }


# --------------------------------------------------------------------------- #
# 位点持久化
# --------------------------------------------------------------------------- #


class CheckpointStore:
    """
    位点存储：单行 JSON，tmp + rename 原子替换，目录 fsync 保证掉盘可见。

    保存内容为已确认 apply 的最大批次的 ``next_lsn``。
    """

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    def load(self) -> int:
        """读取 next_lsn；文件不存在时从 0 开始。"""
        with self._lock:
            if not os.path.exists(self.path):
                return 0
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            next_lsn = int(data["next_lsn"])
            if next_lsn < 0:
                raise ValueError("invalid checkpoint: next_lsn < 0")
            return next_lsn

    def save(self, next_lsn: int) -> None:
        """按批次提交位点（原子写）。"""
        if next_lsn < 0:
            raise ValueError("next_lsn must be >= 0")
        with self._lock:
            directory = os.path.dirname(os.path.abspath(self.path)) or "."
            tmp_path = "%s.tmp.%d" % (self.path, os.getpid())
            payload = json.dumps({"next_lsn": next_lsn}, separators=(",", ":"))
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
            fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)


# --------------------------------------------------------------------------- #
# 日志源（被复制方）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Record:
    lsn: int
    payload: object


@dataclass(frozen=True)
class Batch:
    """一批日志，records 的 lsn 保证在 [start_lsn, end_lsn] 内连续。"""

    start_lsn: int
    end_lsn: int  # 本批最后一条的 lsn
    records: Tuple[Record, ...]
    batch_id: str

    @property
    def next_lsn(self) -> int:
        return self.end_lsn + 1


class LogSource:
    """
    内存日志源，模拟数据库复制流。

    * ``append`` 产生 lsn 连续递增的日志；
    * ``purge_below`` 模拟日志清理（保留窗口之外的记录不可再拉）；
    * ``fetch`` 从 from_lsn 开始返回至多 limit 条；请求已清理位点直接报错，
      绝不从最早可用位置“补”数据给调用方。
    """

    def __init__(self) -> None:
        self._records: Dict[int, Record] = {}
        self._next_lsn = 0
        self._earliest = 0
        self._lock = threading.Lock()

    def append(self, payload: object) -> int:
        with self._lock:
            lsn = self._next_lsn
            self._records[lsn] = Record(lsn, payload)
            self._next_lsn += 1
            return lsn

    def append_many(self, payloads: List[object]) -> int:
        last = -1
        for p in payloads:
            last = self.append(p)
        return last

    @property
    def earliest_available(self) -> int:
        with self._lock:
            return self._earliest

    @property
    def latest_lsn(self) -> int:
        with self._lock:
            return self._next_lsn - 1

    def purge_below(self, keep_from_lsn: int) -> None:
        """删除 lsn < keep_from_lsn 的记录。"""
        with self._lock:
            keep_from_lsn = max(keep_from_lsn, self._earliest)
            for lsn in range(self._earliest, keep_from_lsn):
                self._records.pop(lsn, None)
            self._earliest = keep_from_lsn

    def fetch(
        self, from_lsn: int, limit: int, timeout: float = 5.0
    ) -> Optional[Batch]:
        """
        返回从 from_lsn 起的一批；无数据返回 None。
        from_lsn 落在已清理区间时抛 PositionPurged。
        """
        with self._lock:
            if from_lsn < self._earliest:
                raise PositionPurged(from_lsn, self._earliest)
            if from_lsn >= self._next_lsn:
                return None
            end = min(from_lsn + limit, self._next_lsn)
            records = tuple(self._records[lsn] for lsn in range(from_lsn, end))
            return Batch(
                start_lsn=from_lsn,
                end_lsn=end - 1,
                records=records,
                batch_id="%d-%d" % (from_lsn, end - 1),
            )


# --------------------------------------------------------------------------- #
# 传输层
# --------------------------------------------------------------------------- #


class Transport(Protocol):
    def fetch(
        self, from_lsn: int, limit: int, timeout: float
    ) -> Optional[Batch]: ...


class LocalTransport:
    """同进程直连传输，timeout 透传给源端（真实实现里对应 socket.settimeout）。"""

    def __init__(self, source: LogSource) -> None:
        self.source = source

    def fetch(
        self, from_lsn: int, limit: int, timeout: float
    ) -> Optional[Batch]:
        return self.source.fetch(from_lsn, limit, timeout)


class FlakyTransport:
    """
    故障注入传输，用于自测：
    * timeout_before: 前 N 次调用先模拟阻塞再抛 FetchTimeout；
    * drop_before: 前 N 次调用抛 FetchUnavailable（连接中断）；
    * replay: 调用编号 -> 强制重放某个 Batch（模拟重复投递）；
    * delay: 每次调用的基础延迟（与 timeout 比较，模拟慢响应）。
    """

    def __init__(
        self,
        source: LogSource,
        timeout_before: int = 0,
        drop_before: int = 0,
        replay: Optional[Dict[int, Batch]] = None,
        delay: float = 0.0,
    ) -> None:
        self.source = source
        self.timeout_before = timeout_before
        self.drop_before = drop_before
        self.replay = dict(replay or {})
        self.delay = delay
        self.calls = 0

    def fetch(
        self, from_lsn: int, limit: int, timeout: float
    ) -> Optional[Batch]:
        self.calls += 1
        call_no = self.calls
        if self.delay:
            if self.delay > timeout:
                raise FetchTimeout("simulated slow response > %.3fs" % timeout)
            time.sleep(self.delay)
        if call_no <= self.timeout_before:
            raise FetchTimeout("simulated timeout on call %d" % call_no)
        if call_no <= self.drop_before:
            raise FetchUnavailable("simulated connection reset on call %d" % call_no)
        if call_no in self.replay:
            return self.replay[call_no]
        return self.source.fetch(from_lsn, limit, timeout)


# --------------------------------------------------------------------------- #
# 消费端与拉取器
# --------------------------------------------------------------------------- #


@dataclass
class SequenceSink:
    """
    消费端落点：幂等 apply（按 lsn 去重），是跨重启去重的持久依据。

    真实部署中对应下游的幂等消费（唯一键 / 去重表）。apply 返回
    True 表示新记录，False 表示重复投递被去重。
    """

    applied_lsns: List[int] = field(default_factory=list)
    applied_payloads: List[object] = field(default_factory=list)
    _seen: Set[int] = field(default_factory=set)

    def apply(self, record: Record) -> bool:
        if record.lsn in self._seen:
            return False
        self._seen.add(record.lsn)
        self.applied_lsns.append(record.lsn)
        self.applied_payloads.append(record.payload)
        return True

    @property
    def last_lsn(self) -> int:
        return self.applied_lsns[-1] if self.applied_lsns else -1


@dataclass
class PullStats:
    batches: int = 0
    records: int = 0
    duplicate_batches: int = 0
    duplicate_records: int = 0
    retries: int = 0
    gap: Optional[GapReport] = None

    def merge(self, other: "PullStats") -> None:
        self.batches += other.batches
        self.records += other.records
        self.duplicate_batches += other.duplicate_batches
        self.duplicate_records += other.duplicate_records
        self.retries += other.retries
        if other.gap is not None:
            self.gap = other.gap


class Puller:
    """
    复制日志拉取器。

    每个批次的处理顺序（崩溃安全的关键）：
        fetch -> 去重 apply -> checkpoint.save(next_lsn)
    checkpoint 在 apply 之后提交，因此重启后至多重复一批；
    重复部分由 ``applied_up_to`` 去重。
    """

    def __init__(
        self,
        transport: Transport,
        checkpoint_store: CheckpointStore,
        sink: SequenceSink,
        batch_size: int = 4,
        fetch_timeout: float = 2.0,
        max_retries: int = 3,
        base_backoff: float = 0.01,
        sleep: Callable[[float], None] = time.sleep,
        rng: Optional[random.Random] = None,
    ) -> None:
        self.transport = transport
        self.checkpoint_store = checkpoint_store
        self.sink = sink
        self.batch_size = batch_size
        self.fetch_timeout = fetch_timeout
        self.max_retries = max_retries
        self.base_backoff = base_backoff
        self._sleep = sleep
        self._rng = rng or random.Random(0)
        # 拉取位点只认 checkpoint；去重高水位则参考消费端已持久化的进度
        # （崩溃窗口内 sink 可能已 apply 得比 checkpoint 更远）。
        self._next_lsn = checkpoint_store.load()
        sink_high_water = getattr(sink, "last_lsn", -1)
        self._applied_up_to = max(self._next_lsn - 1, sink_high_water)

    @property
    def position(self) -> int:
        return self._next_lsn

    def pull_once(self, max_batches: Optional[int] = None) -> PullStats:
        """
        尽量向后拉取（追平当前源端）。遇到清理缺口返回带 gap 的 PullStats
        并停止；重试耗尽抛 UnrecoverableFetchError。
        """
        stats = PullStats()
        pulled = 0
        while max_batches is None or pulled < max_batches:
            batch = self._fetch_with_retry(self._next_lsn, stats)
            if batch is None:
                break
            fresh = self._apply_batch(batch, stats)
            stats.records += fresh
            # 位点按批次持久化：无论该批是否全部为重复内容，
            # 都以服务端给出的连续批次末尾推进位点。
            self.checkpoint_store.save(batch.next_lsn)
            self._next_lsn = batch.next_lsn
            self._applied_up_to = batch.end_lsn
            if fresh:
                pulled += 1
            stats.batches += 1
        return stats

    def _fetch_with_retry(
        self, from_lsn: int, stats: PullStats
    ) -> Optional[Batch]:
        attempt = 0
        while True:
            try:
                return self.transport.fetch(from_lsn, self.batch_size,
                                            self.fetch_timeout)
            except PositionPurged as exc:
                # 不可重试：显式产出缺口报告，绝不跳过。
                stats.gap = GapReport(exc.requested_lsn, exc.earliest_available)
                return None
            except (FetchTimeout, FetchUnavailable, TimeoutError,
                    ConnectionError, OSError) as exc:
                if attempt >= self.max_retries:
                    raise UnrecoverableFetchError(
                        "fetch from lsn %d failed after %d retries: %s"
                        % (from_lsn, self.max_retries, exc)
                    ) from exc
                backoff = self.base_backoff * (2 ** attempt)
                backoff += self._rng.random() * self.base_backoff  # 抖动
                stats.retries += 1
                attempt += 1
                self._sleep(backoff)

    def _apply_batch(self, batch: Batch, stats: PullStats) -> int:
        """apply 一批，重复记录由幂等 sink 去重并计数；返回新记录数。"""
        fresh = 0
        if batch.end_lsn <= self._applied_up_to:
            stats.duplicate_batches += 1
        expected = batch.start_lsn
        for record in batch.records:
            # 批内连续性校验：源端给出的批次内部 lsn 必须连续。
            if record.lsn != expected:
                raise RuntimeError(
                    "non-contiguous batch %s: expected lsn %d, got %d"
                    % (batch.batch_id, expected, record.lsn)
                )
            expected += 1
            if self.sink.apply(record):
                fresh += 1
            else:
                stats.duplicate_records += 1
        return fresh
