"""带检查点与续跑的批处理框架（仅标准库）。

核心保证：
1. 检查点记录已完成分片与分片内位点，原子落盘（tmp 文件 + os.replace + fsync）。
2. 已完成分片重复执行不产生副作用（分片级跳过 + 记录级幂等去重双保险）。
3. 续跑从上次中断的分片/位点开始。
4. 检查点损坏时自动备份并安全恢复。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

CHECKPOINT_VERSION = 1


class SimulatedCrash(Exception):
    """测试/演示用：模拟进程中断（断电、kill -9 等）。"""


class CorruptCheckpointError(Exception):
    """检查点文件损坏（非法 JSON、校验和不匹配、版本不符）。"""


@dataclass(frozen=True)
class Record:
    key: str
    value: str


@dataclass(frozen=True)
class Shard:
    name: str
    records: tuple[Record, ...]


def make_shards(num_shards: int, records_per_shard: int, prefix: str = "shard") -> list[Shard]:
    """构造确定性测试数据。"""
    shards = []
    for s in range(num_shards):
        records = tuple(
            Record(key=f"{prefix}-{s}/rec-{r}", value=f"value-{s}-{r}")
            for r in range(records_per_shard)
        )
        shards.append(Shard(name=f"{prefix}-{s}", records=records))
    return shards


class Checkpoint:
    """检查点：已完成分片集合 + 每个分片的已提交位点。

    落盘格式（JSON）::
        {
          "version": 1,
          "job_id": "...",
          "completed_shards": ["shard-0", ...],
          "positions": {"shard-1": 3},
          "checksum": "<sha256 of canonical payload>"
        }
    """

    def __init__(self, path: str, job_id: str = "job"):
        self.path = path
        self.job_id = job_id
        self.completed_shards: list[str] = []
        self.positions: dict[str, int] = {}

    # ---- 状态查询/更新（内存） ----

    def is_completed(self, shard_name: str) -> bool:
        return shard_name in self.completed_shards

    def position(self, shard_name: str) -> int:
        return self.positions.get(shard_name, 0)

    def set_position(self, shard_name: str, position: int) -> None:
        self.positions[shard_name] = position

    def complete_shard(self, shard_name: str) -> None:
        if shard_name not in self.completed_shards:
            self.completed_shards.append(shard_name)
        self.positions.pop(shard_name, None)

    def resume_point(self, shard_names: list[str]) -> tuple[Optional[str], int]:
        """返回续跑起点：(第一个未完成分片, 该分片已提交位点)。全部完成返回 (None, 0)。"""
        for name in shard_names:
            if not self.is_completed(name):
                return name, self.position(name)
        return None, 0

    # ---- 序列化 ----

    def _payload(self) -> dict:
        return {
            "version": CHECKPOINT_VERSION,
            "job_id": self.job_id,
            "completed_shards": list(self.completed_shards),
            "positions": dict(self.positions),
        }

    @staticmethod
    def _checksum(payload: dict) -> str:
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def to_bytes(self) -> bytes:
        payload = self._payload()
        envelope = dict(payload)
        envelope["checksum"] = self._checksum(payload)
        return json.dumps(envelope, indent=2, sort_keys=True).encode("utf-8")

    @classmethod
    def from_bytes(cls, path: str, data: bytes) -> "Checkpoint":
        try:
            envelope = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CorruptCheckpointError(f"检查点不是合法 JSON: {exc}") from exc
        if not isinstance(envelope, dict) or "checksum" not in envelope:
            raise CorruptCheckpointError("检查点缺少 checksum 字段")
        checksum = envelope.pop("checksum")
        if envelope.get("version") != CHECKPOINT_VERSION:
            raise CorruptCheckpointError(f"不支持的检查点版本: {envelope.get('version')!r}")
        if cls._checksum(envelope) != checksum:
            raise CorruptCheckpointError("检查点校验和不匹配（文件可能被截断或篡改）")
        ckpt = cls(path, job_id=envelope.get("job_id", "job"))
        ckpt.completed_shards = list(envelope.get("completed_shards", []))
        ckpt.positions = {str(k): int(v) for k, v in envelope.get("positions", {}).items()}
        return ckpt

    # ---- 原子落盘 ----

    def save(self) -> None:
        """原子写：先写 tmp 并 fsync，再 os.replace，最后 fsync 目录。

        任意一步中断，磁盘上要么是旧的有效检查点，要么是新的有效检查点，
        绝不会出现半写的正式文件；残留的 tmp 文件不影响加载。
        """
        tmp_path = self.path + ".tmp"
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(self.to_bytes())
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            # 写 tmp 失败不影响正式文件；尽力清理残缺的 tmp。
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        os.replace(tmp_path, self.path)  # 同分区原子重命名
        dir_fd = os.open(os.path.dirname(os.path.abspath(self.path)), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    # ---- 加载与恢复 ----

    @classmethod
    def load(cls, path: str, job_id: str = "job", recover: bool = True,
             on_event: Optional[Callable[[dict], None]] = None) -> "Checkpoint":
        """加载检查点。文件不存在 -> 全新；损坏 -> 备份后从零恢复（recover=True）。"""
        if not os.path.exists(path):
            return cls(path, job_id=job_id)
        with open(path, "rb") as f:
            data = f.read()
        try:
            return cls.from_bytes(path, data)
        except CorruptCheckpointError:
            if not recover:
                raise
            backup = f"{path}.corrupt.{int(time.time() * 1000)}"
            os.replace(path, backup)
            if on_event:
                on_event({"event": "checkpoint_recovered", "backup": backup})
            return cls(path, job_id=job_id)


class RecordSink:
    """幂等输出汇：按记录 key 去重，重复写入是 no-op。

    模拟真实场景里"写库/写文件"的副作用：已存在的 key 不会再写第二遍，
    因此即使在分片中间崩溃重跑，也不会产生重复行。
    """

    def __init__(self, path: str):
        self.path = path
        self._seen: set[str] = set()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.rstrip("\n")
                    if line:
                        self._seen.add(line.split("\t", 1)[0])

    def write(self, record: Record) -> bool:
        """写入一条记录；key 已存在则跳过。返回是否真正写入。"""
        if record.key in self._seen:
            return False
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(f"{record.key}\t{record.value}\n")
            f.flush()
            os.fsync(f.fileno())
        self._seen.add(record.key)
        return True

    def lines(self) -> list[str]:
        if not os.path.exists(self.path):
            return []
        with open(self.path, "r", encoding="utf-8") as f:
            return [line.rstrip("\n") for line in f if line.strip()]

    def read_bytes(self) -> bytes:
        if not os.path.exists(self.path):
            return b""
        with open(self.path, "rb") as f:
            return f.read()


class CrashInjector:
    """故障注入：在第 fail_after 次记录提交后抛出 SimulatedCrash。"""

    def __init__(self, fail_after: Optional[int] = None):
        self.fail_after = fail_after
        self.commits = 0

    def after_commit(self) -> None:
        self.commits += 1
        if self.fail_after is not None and self.commits >= self.fail_after:
            raise SimulatedCrash(f"模拟中断：第 {self.commits} 次提交后崩溃")


class BatchJob:
    """批处理任务：按分片顺序处理，逐条提交并维护检查点。"""

    def __init__(
        self,
        shards: Iterable[Shard],
        checkpoint: Checkpoint,
        sink: RecordSink,
        injector: Optional[CrashInjector] = None,
        on_event: Optional[Callable[[dict], None]] = None,
    ):
        self.shards = list(shards)
        self.checkpoint = checkpoint
        self.sink = sink
        self.injector = injector or CrashInjector()
        self._on_event = on_event or (lambda e: None)

    def _emit(self, event: dict) -> None:
        self._on_event(event)

    def run(self) -> dict:
        """执行（或续跑）任务。返回运行摘要。"""
        names = [s.name for s in self.shards]
        resume_shard, resume_pos = self.checkpoint.resume_point(names)
        self._emit({"event": "resume", "shard": resume_shard, "position": resume_pos})

        written = 0
        skipped_duplicates = 0
        for shard in self.shards:
            if self.checkpoint.is_completed(shard.name):
                self._emit({"event": "shard_skip", "shard": shard.name})
                continue
            start = self.checkpoint.position(shard.name)
            self._emit({"event": "shard_begin", "shard": shard.name, "position": start})
            for idx in range(start, len(shard.records)):
                record = shard.records[idx]
                if self.sink.write(record):
                    written += 1
                else:
                    skipped_duplicates += 1
                self.checkpoint.set_position(shard.name, idx + 1)
                self.checkpoint.save()
                self._emit({"event": "record_commit", "shard": shard.name, "position": idx + 1})
                self.injector.after_commit()
            self.checkpoint.complete_shard(shard.name)
            self.checkpoint.save()
            self._emit({"event": "shard_done", "shard": shard.name})
        self._emit({"event": "job_done"})
        return {"written": written, "skipped_duplicates": skipped_duplicates}
