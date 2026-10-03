"""MVCC transaction snapshots with snapshot-based visibility and GC pinning.

Snapshot isolation, implemented in pure Python (standard library only).

Model
-----
* Every write goes through a :class:`Transaction`.  On ``commit`` the store
  assigns a monotonically increasing commit timestamp (``commit_ts``) and the
  transaction becomes the owner of the new key versions.
* When a transaction begins it takes a :class:`Snapshot`.  The snapshot
  records the *explicit set* of transaction ids that were committed at that
  moment.  Read visibility is decided strictly by membership in that set
  (plus the transaction's own uncommitted writes).
* Old versions are garbage collected by :meth:`MVCCStore.gc`.  The oldest
  active snapshot defines the cleanup boundary; nothing that snapshot can
  still read is deleted.
"""

from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional


class SnapshotExpiredError(RuntimeError):
    """Raised when a read is attempted through an expired snapshot."""


class TransactionClosedError(RuntimeError):
    """Raised when a transaction is used after commit/abort."""


# Sentinels for keys that never existed / were deleted inside the txn.
_UNSET = object()
_TOMBSTONE = object()


@dataclass(frozen=True)
class Version:
    """One historical value of a key."""

    txn_id: int
    commit_ts: int
    value: Any = None
    deleted: bool = False


@dataclass(frozen=True)
class Snapshot:
    """Read view: the committed transactions visible to one transaction."""

    snapshot_id: int
    boundary: int
    # Explicit set of committed transaction ids; visibility is membership in
    # this set, never a comparison against "current" state.
    committed_txns: FrozenSet[int]
    # Max number of commits that may happen while this snapshot is alive.
    ttl: Optional[int] = None
    created_seq: int = 0

    def sees(self, version: Version) -> bool:
        """Strict visibility check for a committed version."""
        return version.txn_id in self.committed_txns


@dataclass
class _TxnRecord:
    txn_id: int
    state: str = "active"  # active | committed | aborted
    commit_ts: Optional[int] = None


class Transaction:
    def __init__(
        self,
        store: "MVCCStore",
        txn_id: int,
        snapshot: Snapshot,
    ) -> None:
        self._store = store
        self.txn_id = txn_id
        self.snapshot = snapshot
        self._writes: Dict[Any, Any] = {}
        self._closed = False

    # -- lifecycle -------------------------------------------------------

    def __enter__(self) -> "Transaction":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self._closed:
            if exc_type is None:
                self.commit()
            else:
                self.abort()

    def commit(self) -> int:
        self._require_open()
        ts = self._store._commit_transaction(self)
        self._closed = True
        return ts

    def abort(self) -> None:
        if self._closed:
            return
        self._store._abort_transaction(self)
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed

    # -- reads / writes --------------------------------------------------

    def read(self, key: Any, default: Any = None) -> Any:
        """Read at the snapshot. Returns ``default`` for absent/deleted keys."""
        self._require_open()
        self._store._check_snapshot_alive(self.snapshot)

        if key in self._writes:
            local = self._writes[key]
            return default if local is _TOMBSTONE else local

        for version in reversed(self._store._versions_for(key)):
            if self.snapshot.sees(version):
                return default if version.deleted else version.value
        return default

    def exists(self, key: Any) -> bool:
        self._require_open()
        self._store._check_snapshot_alive(self.snapshot)
        if key in self._writes:
            return self._writes[key] is not _TOMBSTONE
        for version in reversed(self._store._versions_for(key)):
            if self.snapshot.sees(version):
                return not version.deleted
        return False

    def write(self, key: Any, value: Any) -> None:
        self._require_open()
        self._writes[key] = value

    def delete(self, key: Any) -> None:
        self._require_open()
        self._writes[key] = _TOMBSTONE

    def _require_open(self) -> None:
        if self._closed:
            raise TransactionClosedError(f"transaction {self.txn_id} is closed")


class MVCCStore:
    """Thread-safe in-memory MVCC key/value store."""

    def __init__(self, default_ttl: Optional[int] = None) -> None:
        self._lock = threading.RLock()
        self._data: Dict[Any, List[Version]] = {}
        self._txns: Dict[int, _TxnRecord] = {}
        self._snapshots: Dict[int, Snapshot] = {}
        self._commit_seq = 0
        self._txn_counter = itertools.count(1)
        self._snapshot_counter = itertools.count(1)
        self._default_ttl = default_ttl

    # -- transactions ----------------------------------------------------

    def begin(self, ttl: Optional[int] = _UNSET) -> Transaction:
        """Start a transaction, fixing its snapshot at the current commit point."""
        with self._lock:
            txn_id = next(self._txn_counter)
            self._txns[txn_id] = _TxnRecord(txn_id)
            committed = frozenset(
                rec.txn_id
                for rec in self._txns.values()
                if rec.state == "committed"
            )
            effective_ttl = self._default_ttl if ttl is _UNSET else ttl
            snapshot = Snapshot(
                snapshot_id=next(self._snapshot_counter),
                boundary=self._commit_seq,
                committed_txns=committed,
                ttl=effective_ttl,
                created_seq=self._commit_seq,
            )
            self._snapshots[snapshot.snapshot_id] = snapshot
            return Transaction(self, txn_id, snapshot)

    def _commit_transaction(self, txn: Transaction) -> int:
        with self._lock:
            self._check_snapshot_alive(txn.snapshot)
            record = self._txns[txn.txn_id]
            self._commit_seq += 1
            record.state = "committed"
            record.commit_ts = self._commit_seq
            for key, staged in txn._writes.items():
                version = Version(
                    txn_id=txn.txn_id,
                    commit_ts=self._commit_seq,
                    value=None if staged is _TOMBSTONE else staged,
                    deleted=staged is _TOMBSTONE,
                )
                self._data.setdefault(key, []).append(version)
            self._snapshots.pop(txn.snapshot.snapshot_id, None)
            return self._commit_seq

    def _abort_transaction(self, txn: Transaction) -> None:
        with self._lock:
            record = self._txns[txn.txn_id]
            record.state = "aborted"
            self._snapshots.pop(txn.snapshot.snapshot_id, None)

    # -- snapshots -------------------------------------------------------

    def active_snapshots(self) -> List[Snapshot]:
        with self._lock:
            self._purge_expired_snapshots()
            return list(self._snapshots.values())

    def _check_snapshot_alive(self, snapshot: Snapshot) -> None:
        if snapshot.ttl is not None:
            age = self._commit_seq - snapshot.created_seq
            if age > snapshot.ttl:
                self._snapshots.pop(snapshot.snapshot_id, None)
                raise SnapshotExpiredError(
                    f"snapshot {snapshot.snapshot_id} expired after {age} "
                    f"commits (ttl={snapshot.ttl})"
                )

    def _purge_expired_snapshots(self) -> None:
        expired = [
            snap.snapshot_id
            for snap in self._snapshots.values()
            if snap.ttl is not None
            and self._commit_seq - snap.created_seq > snap.ttl
        ]
        for snap_id in expired:
            self._snapshots.pop(snap_id, None)

    # -- garbage collection ----------------------------------------------

    def gc(self) -> int:
        """Delete versions not needed by any active or future snapshot.

        Boundary: a version is *needed* iff it is the newest version visible
        to some active snapshot (i.e. the newest with ``commit_ts <=`` the
        snapshot's ``boundary``) or it is the newest committed version
        overall (needed by future snapshots).  Every other version is
        garbage.  Equivalently, the oldest active snapshot pins the cleanup
        boundary: nothing it can still read is ever deleted.
        """
        with self._lock:
            self._purge_expired_snapshots()
            boundaries = [s.boundary for s in self._snapshots.values()]
            boundaries.append(self._commit_seq)  # future snapshots

            removed = 0
            empty_keys = []
            for key, versions in self._data.items():
                keep = set()
                for boundary in boundaries:
                    newest = None
                    for index, version in enumerate(versions):
                        if version.commit_ts <= boundary:
                            newest = index
                    if newest is not None:
                        keep.add(newest)
                survivors = [v for i, v in enumerate(versions) if i in keep]
                removed += len(versions) - len(survivors)
                if survivors:
                    self._data[key] = survivors
                else:
                    empty_keys.append(key)
            for key in empty_keys:
                del self._data[key]
            return removed

    # -- inspection (tests / demos) --------------------------------------

    def _versions_for(self, key: Any) -> List[Version]:
        return self._data.get(key, [])

    def committed_count(self) -> int:
        with self._lock:
            return self._commit_seq

    def version_history(self, key: Any) -> List[Version]:
        with self._lock:
            return list(self._data.get(key, []))
