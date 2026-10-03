"""snapshot_mvcc 自测：可见性判定表、清理边界、并发观察、快照过期。

运行：python3 -m unittest test_snapshot_mvcc -v
"""

import threading
import time
import unittest

from snapshot_mvcc import (
    MVCCStore,
    SnapshotExpiredError,
    TransactionStateError,
)


def commit(store, **kv):
    txn = store.begin()
    for key, value in kv.items():
        txn.write(key, value)
    return txn.commit()


class VisibilityTableTest(unittest.TestCase):
    """可见性判定表逐行验证。

    判定表（S = 快照可见集合）：
    | # | 版本状态                | commit_id ∈ S | 判定                     |
    |---|-------------------------|---------------|--------------------------|
    | 1 | 快照前已提交            | 是            | 可见，取最新可见版本     |
    | 2 | 快照后新提交            | 否            | 不可见                   |
    | 3 | 其他事务未提交          | 否            | 不可见                   |
    | 4 | 本事务未提交写          | —（本地缓冲） | 仅本事务可见             |
    | 5 | 最新可见版本为删除标记  | 是            | 读取结果为“不存在”       |
    | 6 | 键无任何可见版本        | —             | 读取结果为“不存在”       |
    """

    def setUp(self):
        self.store = MVCCStore()

    def test_row1_committed_before_snapshot_visible(self):
        commit(self.store, k="v1")
        commit(self.store, k="v2")
        txn = self.store.begin()
        self.assertEqual(txn.snapshot.visible_commits, frozenset({1, 2}))
        self.assertEqual(txn.read("k"), "v2")  # 取最新可见版本

    def test_row2_committed_after_snapshot_invisible(self):
        commit(self.store, k="old")
        txn = self.store.begin()
        commit(self.store, k="new")  # 快照建立后的新提交
        self.assertEqual(txn.read("k"), "old")

    def test_row3_uncommitted_other_txn_invisible(self):
        commit(self.store, k="base")
        reader = self.store.begin()
        writer = self.store.begin()
        writer.write("k", "dirty")
        self.assertEqual(reader.read("k"), "base")  # 未提交数据不可见
        writer.abort()

    def test_row4_own_uncommitted_write_visible_to_self(self):
        commit(self.store, k="base")
        txn = self.store.begin()
        txn.write("k", "mine")
        self.assertEqual(txn.read("k"), "mine")  # 本事务可见
        other = self.store.begin()
        self.assertEqual(other.read("k"), "base")  # 其他事务不可见

    def test_row5_visible_tombstone_reads_as_missing(self):
        commit(self.store, k="v")
        txn = self.store.begin()
        txn.delete("k")
        txn.commit()
        reader = self.store.begin()
        self.assertIsNone(reader.read("k"))
        # 但旧快照仍读到删除前的值
        old = self.store.begin()
        old.snapshot.visible_commits = frozenset({1})  # 模拟只含 v1 的快照
        self.assertEqual(old.read("k"), "v")

    def test_row6_no_visible_version_reads_as_missing(self):
        txn = self.store.begin()  # 快照为空集
        commit(self.store, k="v")
        self.assertIsNone(txn.read("k"))
        self.assertIsNone(txn.read("never-existed"))


class NoConcurrencyTest(unittest.TestCase):
    """情形一：无并发，顺序提交与读取。"""

    def test_sequential_commits_and_snapshot_stability(self):
        store = MVCCStore()
        commit(store, a=1)
        commit(store, b=2)
        txn = store.begin()
        self.assertEqual(txn.read("a"), 1)
        self.assertEqual(txn.read("b"), 2)
        commit(store, a=100)
        self.assertEqual(txn.read("a"), 1)  # 同一快照重复读结果一致
        self.assertEqual(txn.read("a"), 1)
        txn.commit()
        self.assertEqual(store.begin().read("a"), 100)

    def test_commit_returns_monotonic_ids(self):
        store = MVCCStore()
        ids = [commit(store, k=i) for i in range(5)]
        self.assertEqual(ids, [1, 2, 3, 4, 5])


class ConcurrentWriteTest(unittest.TestCase):
    """情形二：并发写入。长事务读期间，另一线程提交新值，互不影响。"""

    def test_concurrent_commit_does_not_change_snapshot_reads(self):
        store = MVCCStore()
        commit(store, k="v0")
        started = threading.Barrier(2)
        observations = {}

        def long_reader():
            txn = store.begin()
            started.wait()          # 双方同时进入临界区
            time.sleep(0.05)        # 让写线程先提交
            observations["during"] = txn.read("k")
            observations["again"] = txn.read("k")
            txn.commit()

        def writer():
            started.wait()
            commit(store, k="v1")
            commit(store, k="v2")

        t1 = threading.Thread(target=long_reader)
        t2 = threading.Thread(target=writer)
        t1.start(); t2.start(); t1.join(); t2.join()

        self.assertEqual(observations, {"during": "v0", "again": "v0"})
        self.assertEqual(store.begin().read("k"), "v2")

    def test_snapshot_diff_observation_data(self):
        """情形三延伸：两个不同时刻的快照，观察结果可对比。"""
        store = MVCCStore()
        commit(store, x="x1", y="y1")
        snap_old_txn = store.begin()           # 快照 A：{1}
        commit(store, x="x2")
        commit(store, y="y2", z="z2")
        snap_new_txn = store.begin()           # 快照 B：{1,2,3}

        table = []
        for key in ("x", "y", "z"):
            table.append(
                (key, snap_old_txn.read(key), snap_new_txn.read(key))
            )
        # 观察结果数据（键, 快照A读到, 快照B读到）
        self.assertEqual(
            table,
            [("x", "x1", "x2"), ("y", "y1", "y2"), ("z", None, "z2")],
        )
        self.assertNotEqual(
            snap_old_txn.snapshot.visible_commits,
            snap_new_txn.snapshot.visible_commits,
        )


class LongAndShortTxnTest(unittest.TestCase):
    """情形三：长事务与短事务混合，长事务期间短事务反复提交。"""

    def test_long_txn_stable_while_short_txns_churn(self):
        store = MVCCStore()
        commit(store, counter=0)
        long_txn = store.begin()
        for i in range(1, 11):  # 10 个短事务连续提交
            commit(store, counter=i)
            self.assertEqual(long_txn.read("counter"), 0)
        self.assertEqual(store.version_count("counter"), 11)  # 长事务钉住旧版本
        long_txn.commit()
        self.assertEqual(store.begin().read("counter"), 10)


class GCBoundaryTest(unittest.TestCase):
    """清理边界与快照生命周期。"""

    def test_gc_blocked_by_oldest_snapshot(self):
        store = MVCCStore()
        commit(store, k="v1")
        long_txn = store.begin()          # 快照可见集合 {1}
        commit(store, k="v2")
        commit(store, k="v3")
        result = store.gc()
        # v2 对长快照不可见且被 v3 覆盖，可回收；v1 被长事务钉住，v3 是最新版本
        self.assertEqual(result["removed_versions"], 1)
        self.assertEqual(
            [v.commit_id for v in store.versions_of("k")], [1, 3]
        )
        self.assertEqual(long_txn.read("k"), "v1")
        long_txn.commit()

    def test_gc_collects_after_snapshot_close(self):
        store = MVCCStore()
        commit(store, k="v1")
        long_txn = store.begin()
        commit(store, k="v2")
        commit(store, k="v3")
        long_txn.commit()                  # 快照关闭，解除钉住
        result = store.gc()
        self.assertEqual(result["removed_versions"], 2)  # v1、v2 被回收
        self.assertEqual(store.version_count("k"), 1)
        self.assertEqual(store.begin().read("k"), "v3")

    def test_gc_boundary_exact(self):
        """边界用例：边界上的版本保留，边界下被覆盖的版本回收。"""
        store = MVCCStore()
        commit(store, a="a1", b="b1")          # commit 1
        snap_txn = store.begin()               # 可见集合 {1}，horizon = 2
        commit(store, a="a2")                  # commit 2
        commit(store, a="a3", b="b2")          # commit 3
        self.assertEqual(store.gc_horizon(), 2)
        store.gc()
        # a：v1（快照需要）+ v3（最新）保留，v2 被回收
        self.assertEqual(
            [v.commit_id for v in store.versions_of("a")], [1, 3]
        )
        # b：v1（快照需要）+ v2（最新）保留
        self.assertEqual(
            [v.commit_id for v in store.versions_of("b")], [1, 3]
        )
        self.assertEqual(snap_txn.read("a"), "a1")
        self.assertEqual(snap_txn.read("b"), "b1")
        snap_txn.commit()

    def test_gc_removes_fully_deleted_key(self):
        store = MVCCStore()
        commit(store, k="v")
        txn = store.begin()
        txn.delete("k")
        txn.commit()
        result = store.gc()                    # 无存活快照
        self.assertEqual(result["removed_keys"], 1)
        self.assertEqual(store.version_count("k"), 0)
        self.assertIsNone(store.begin().read("k"))

    def test_gc_keeps_tombstone_while_snapshot_needs_old_value(self):
        store = MVCCStore()
        commit(store, k="v")
        old_txn = store.begin()                # 仍需要 v1
        txn = store.begin()
        txn.delete("k")
        txn.commit()
        store.gc()
        self.assertEqual(store.version_count("k"), 2)  # 值版本 + 墓碑都保留
        self.assertEqual(old_txn.read("k"), "v")
        old_txn.commit()
        store.gc()
        self.assertEqual(store.version_count("k"), 0)  # 快照关闭后整键清除


class SnapshotExpiryTest(unittest.TestCase):
    """情形四：快照过期。"""

    def test_expired_snapshot_rejects_reads(self):
        store = MVCCStore()
        commit(store, k="v")
        txn = store.begin(ttl_seconds=0.05)
        self.assertEqual(txn.read("k"), "v")
        time.sleep(0.08)
        with self.assertRaises(SnapshotExpiredError):
            txn.read("k")

    def test_expired_snapshot_unblocks_gc(self):
        store = MVCCStore()
        commit(store, k="v1")
        txn = store.begin(ttl_seconds=0.05)    # 快照钉住 v1
        commit(store, k="v2")
        self.assertEqual(store.gc()["removed_versions"], 0)
        time.sleep(0.08)                       # 快照过期，不再钉住
        self.assertEqual(store.gc()["removed_versions"], 1)
        self.assertEqual(store.version_count("k"), 1)

    def test_no_ttl_snapshot_never_expires(self):
        store = MVCCStore()
        commit(store, k="v")
        txn = store.begin()
        time.sleep(0.05)
        self.assertEqual(txn.read("k"), "v")

    def test_use_after_commit_raises(self):
        store = MVCCStore()
        txn = store.begin()
        txn.write("k", "v")
        txn.commit()
        with self.assertRaises(TransactionStateError):
            txn.read("k")


if __name__ == "__main__":
    unittest.main()
