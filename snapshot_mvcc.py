"""MVCC 事务快照库（仅标准库）。

核心概念：
- 每个键保存一条按 commit_id 升序排列的版本链，版本可能是值或删除标记。
- 事务开始时创建快照：快照记录当时已提交的 commit_id 集合（可见集合）。
- 读取严格按快照的可见集合判定：commit_id 在集合内才可见，集合外一律不可见。
- 清理（GC）：被任意存活快照需要的版本不得删除；快照关闭或过期后解除钉住。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Hashable, List, Optional

TOMBSTONE = object()  # 删除标记


class SnapshotExpiredError(Exception):
    """快照已过期，禁止再读取。"""


class TransactionStateError(Exception):
    """事务状态非法（如在已提交/已中止后继续使用）。"""


@dataclass(frozen=True)
class Version:
    commit_id: int
    value: Any  # TOMBSTONE 表示删除

    @property
    def deleted(self) -> bool:
        return self.value is TOMBSTONE


class Snapshot:
    """事务快照：记录创建瞬间可见的提交版本集合。"""

    def __init__(
        self,
        store: "MVCCStore",
        snap_id: int,
        visible_commits: FrozenSet[int],
        ttl_seconds: Optional[float] = None,
    ) -> None:
        self._store = store
        self.snap_id = snap_id
        self.visible_commits = visible_commits  # 可见的 commit_id 集合
        self.created_at = time.monotonic()
        self.expires_at = (
            self.created_at + ttl_seconds if ttl_seconds is not None else None
        )
        self._closed = False

    def is_visible(self, commit_id: int) -> bool:
        """可见性判定：严格按快照记录的提交集合。"""
        return commit_id in self.visible_commits

    def is_expired(self) -> bool:
        return self.expires_at is not None and time.monotonic() >= self.expires_at

    def is_active(self) -> bool:
        return not self._closed and not self.is_expired()

    def check_alive(self) -> None:
        if self._closed:
            raise TransactionStateError("快照已关闭")
        if self.is_expired():
            raise SnapshotExpiredError(
                f"快照 {self.snap_id} 已过期（ttl 到期）"
            )

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._store._unregister_snapshot(self)

    def __repr__(self) -> str:
        return (
            f"Snapshot(id={self.snap_id}, visible={sorted(self.visible_commits)}, "
            f"active={self.is_active()})"
        )


class Transaction:
    """单个事务：begin 时建立快照，写操作先落本地缓冲，commit 时统一分配 commit_id。"""

    def __init__(self, store: "MVCCStore", ttl_seconds: Optional[float] = None) -> None:
        self._store = store
        self.snapshot = store._take_snapshot(ttl_seconds)
        self._writes: Dict[Hashable, Any] = {}
        self._state = "active"  # active | committed | aborted

    def _check_active(self) -> None:
        if self._state != "active":
            raise TransactionStateError(f"事务已{self._state}，不能再操作")
        self.snapshot.check_alive()

    def read(self, key: Hashable) -> Any:
        """按快照可见集合读取；本事务未提交的写优先（read-your-own-writes）。"""
        self._check_active()
        if key in self._writes:
            value = self._writes[key]
            return None if value is TOMBSTONE else value
        return self._store._read_with_snapshot(self.snapshot, key)

    def write(self, key: Hashable, value: Any) -> None:
        self._check_active()
        self._writes[key] = value

    def delete(self, key: Hashable) -> None:
        self._check_active()
        self._writes[key] = TOMBSTONE

    def commit(self) -> int:
        self._check_active()
        commit_id = self._store._commit(self._writes)
        self._state = "committed"
        self.snapshot.close()
        return commit_id

    def abort(self) -> None:
        self._check_active()
        self._state = "aborted"
        self._writes.clear()
        self.snapshot.close()

    def __enter__(self) -> "Transaction":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._state != "active":
            return
        if exc_type is None:
            self.commit()
        else:
            self.abort()


class MVCCStore:
    """多版本键值存储，提供快照读与基于快照生命周期的垃圾清理。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: Dict[Hashable, List[Version]] = {}
        self._commit_ids: List[int] = []  # 已分配的 commit_id，升序
        self._committed: set[int] = set()
        self._next_commit_id = 0
        self._next_snap_id = 0
        self._snapshots: Dict[int, Snapshot] = {}

    # ---- 事务接口 ----

    def begin(self, ttl_seconds: Optional[float] = None) -> Transaction:
        return Transaction(self, ttl_seconds)

    # ---- 内部实现 ----

    def _take_snapshot(self, ttl_seconds: Optional[float]) -> Snapshot:
        with self._lock:
            self._next_snap_id += 1
            snap = Snapshot(
                self,
                self._next_snap_id,
                frozenset(self._committed),
                ttl_seconds,
            )
            self._snapshots[snap.snap_id] = snap
            return snap

    def _unregister_snapshot(self, snap: Snapshot) -> None:
        with self._lock:
            self._snapshots.pop(snap.snap_id, None)

    def _read_with_snapshot(self, snap: Snapshot, key: Hashable) -> Any:
        with self._lock:
            versions = self._data.get(key)
            if not versions:
                return None
            for version in reversed(versions):  # 从最新往最旧找第一个可见版本
                if snap.is_visible(version.commit_id):
                    return None if version.deleted else version.value
            return None

    def _commit(self, writes: Dict[Hashable, Any]) -> int:
        with self._lock:
            self._next_commit_id += 1
            commit_id = self._next_commit_id
            for key, value in writes.items():
                self._data.setdefault(key, []).append(Version(commit_id, value))
            self._commit_ids.append(commit_id)
            self._committed.add(commit_id)
            return commit_id

    # ---- 垃圾清理 ----

    def active_snapshots(self) -> List[Snapshot]:
        with self._lock:
            # 过期快照不再钉住数据，视为不存活
            return [s for s in self._snapshots.values() if s.is_active()]

    def gc_horizon(self) -> int:
        """清理边界：所有存活快照可见集合中最小的最大 commit_id。

        含义：commit_id < horizon 且被更新版本覆盖的旧版本可以被回收；
        没有任何存活快照时 horizon = 当前最大 commit_id + 1（只保留每键最新版本）。
        """
        with self._lock:
            actives = self.active_snapshots()
            if not actives:
                return self._next_commit_id + 1
            return min(
                (max(s.visible_commits) if s.visible_commits else 0) + 1
                for s in actives
            )

    def gc(self) -> Dict[str, int]:
        """回收不再被任何存活快照需要的版本。

        规则（对每个键）：
        1. 最新版本永远保留（供未来快照读取）；
        2. 每个存活快照可见集合中该键的最新版本必须保留；
        3. 其余版本回收；若剩余版本全是删除标记，则整个键移除。
        """
        removed_versions = 0
        removed_keys = 0
        with self._lock:
            actives = self.active_snapshots()
            for key in list(self._data.keys()):
                versions = self._data[key]
                keep_ids = {versions[-1].commit_id}
                for snap in actives:
                    for version in reversed(versions):
                        if snap.is_visible(version.commit_id):
                            keep_ids.add(version.commit_id)
                            break
                kept = [v for v in versions if v.commit_id in keep_ids]
                removed_versions += len(versions) - len(kept)
                if all(v.deleted for v in kept):
                    del self._data[key]
                    removed_keys += 1
                    removed_versions += len(kept)
                else:
                    self._data[key] = kept
        return {"removed_versions": removed_versions, "removed_keys": removed_keys}

    # ---- 观测辅助 ----

    def version_count(self, key: Hashable) -> int:
        with self._lock:
            return len(self._data.get(key, []))

    def versions_of(self, key: Hashable) -> List[Version]:
        with self._lock:
            return list(self._data.get(key, []))
