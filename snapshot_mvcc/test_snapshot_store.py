"""Self-tests for snapshot_store: visibility table, GC boundary, lifecycle,
concurrency, long/short transaction mixes, snapshot expiry, edge cases.

Run:  python3 -m unittest test_snapshot_store -v
"""

import threading
import unittest

from snapshot_store import (
    MVCCStore,
    SnapshotExpiredError,
    TransactionClosedError,
    Version,
)


def commit_value(store, key, value):
    txn = store.begin()
    txn.write(key, value)
    txn.commit()
    return txn


class VisibilityDecisionTableTest(unittest.TestCase):
    """Every row of the visibility decision table, executed as a scenario.

    | # | version origin                | in snapshot set | own txn | visible |
    |---|-------------------------------|-----------------|---------|---------|
    | 1 | committed before snapshot     | yes             | no      | yes     |
    | 2 | committed after snapshot      | no              | no      | no      |
    | 3 | aborted transaction           | no              | no      | no      |
    | 4 | active (uncommitted) other txn| no              | no      | no      |
    | 5 | own uncommitted write         | n/a             | yes     | yes     |
    | 6 | own uncommitted delete        | n/a             | yes     | yes (as absent) |
    | 7 | deleted version (committed)   | yes             | no      | yes (as absent) |
    """

    def test_row1_committed_before_snapshot_visible(self):
        store = MVCCStore()
        commit_value(store, "k", "v1")
        reader = store.begin()
        self.assertEqual(reader.read("k"), "v1")

    def test_row2_committed_after_snapshot_invisible(self):
        store = MVCCStore()
        commit_value(store, "k", "v1")
        reader = store.begin()
        commit_value(store, "k", "v2")
        self.assertEqual(reader.read("k"), "v1")

    def test_row3_aborted_transaction_invisible(self):
        store = MVCCStore()
        commit_value(store, "k", "v1")
        writer = store.begin()
        writer.write("k", "dirty")
        writer.abort()
        reader = store.begin()
        self.assertEqual(reader.read("k"), "v1")

    def test_row4_uncommitted_other_transaction_invisible(self):
        store = MVCCStore()
        commit_value(store, "k", "v1")
        writer = store.begin()
        writer.write("k", "uncommitted")
        reader = store.begin()
        self.assertEqual(reader.read("k"), "v1")
        writer.abort()

    def test_row5_own_uncommitted_write_visible(self):
        store = MVCCStore()
        commit_value(store, "k", "v1")
        txn = store.begin()
        txn.write("k", "mine")
        self.assertEqual(txn.read("k"), "mine")

    def test_row6_own_uncommitted_delete_visible_as_absent(self):
        store = MVCCStore()
        commit_value(store, "k", "v1")
        txn = store.begin()
        txn.delete("k")
        self.assertIsNone(txn.read("k"))
        self.assertFalse(txn.exists("k"))

    def test_row7_committed_delete_visible_as_absent(self):
        store = MVCCStore()
        commit_value(store, "k", "v1")
        txn = store.begin()
        txn.delete("k")
        txn.commit()
        reader = store.begin()
        self.assertIsNone(reader.read("k"))
        self.assertFalse(reader.exists("k"))

    def test_snapshot_set_is_frozen_at_begin(self):
        store = MVCCStore()
        t1 = commit_value(store, "k", "v1")
        snap = store.begin().snapshot
        t2 = commit_value(store, "k", "v2")
        self.assertIn(t1.txn_id, snap.committed_txns)
        self.assertNotIn(t2.txn_id, snap.committed_txns)
        version_v2 = store.version_history("k")[-1]
        self.assertFalse(snap.sees(version_v2))


class NoConcurrencyTest(unittest.TestCase):
    def test_write_then_read_same_value(self):
        store = MVCCStore()
        commit_value(store, "a", 1)
        self.assertEqual(store.begin().read("a"), 1)

    def test_missing_key_returns_default(self):
        store = MVCCStore()
        self.assertIsNone(store.begin().read("nope"))
        self.assertEqual(store.begin().read("nope", default=42), 42)

    def test_sequential_overwrites_latest_wins(self):
        store = MVCCStore()
        for value in ("v1", "v2", "v3"):
            commit_value(store, "k", value)
        self.assertEqual(store.begin().read("k"), "v3")

    def test_context_manager_commits_on_success(self):
        store = MVCCStore()
        with store.begin() as txn:
            txn.write("k", "v")
        self.assertEqual(store.begin().read("k"), "v")

    def test_context_manager_aborts_on_exception(self):
        store = MVCCStore()
        commit_value(store, "k", "v1")
        with self.assertRaises(ValueError):
            with store.begin() as txn:
                txn.write("k", "bad")
                raise ValueError("boom")
        self.assertEqual(store.begin().read("k"), "v1")


class ConcurrentWriteTest(unittest.TestCase):
    def test_concurrent_writers_all_commit_no_lost_versions(self):
        store = MVCCStore()
        writers = 8
        barrier = threading.Barrier(writers)

        def worker(n):
            txn = store.begin()
            txn.write(f"key-{n % 3}", n)
            barrier.wait()
            txn.commit()

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(writers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(store.committed_count(), writers)
        for key in ("key-0", "key-1", "key-2"):
            history = store.version_history(key)
            self.assertEqual(len({v.commit_ts for v in history}), len(history))
            self.assertGreaterEqual(len(history), 1)

    def test_concurrent_readers_never_see_partial_commit(self):
        store = MVCCStore()
        commit_value(store, "x", 0)
        commit_value(store, "y", 0)
        errors = []
        stop = threading.Event()

        def writer():
            n = 0
            while not stop.is_set():
                n += 1
                txn = store.begin()
                txn.write("x", n)
                txn.write("y", n)
                txn.commit()

        def reader():
            while not stop.is_set():
                txn = store.begin()
                x, y = txn.read("x"), txn.read("y")
                if x != y:
                    errors.append((x, y))
                txn.abort()

        threads = [threading.Thread(target=writer)] + [
            threading.Thread(target=reader) for _ in range(4)
        ]
        for t in threads:
            t.start()
        threading.Event().wait(0.3)
        stop.set()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


class LongAndShortTransactionTest(unittest.TestCase):
    def test_long_snapshot_stable_while_short_snapshots_advance(self):
        store = MVCCStore()
        commit_value(store, "counter", 0)

        long_txn = store.begin()
        observations = []

        for step in range(1, 6):
            commit_value(store, "counter", step)
            short_txn = store.begin()
            observations.append(
                {
                    "commit": step,
                    "long_txn_sees": long_txn.read("counter"),
                    "short_txn_sees": short_txn.read("counter"),
                }
            )
            short_txn.abort()

        self.assertEqual([o["long_txn_sees"] for o in observations], [0] * 5)
        self.assertEqual([o["short_txn_sees"] for o in observations], [1, 2, 3, 4, 5])
        long_txn.abort()

    def test_interleaved_long_short_with_observation_log(self):
        """Produces the observation table used in the docs; asserts it."""
        store = MVCCStore()
        commit_value(store, "acct", 100)

        long_reader = store.begin()
        log = []

        def observe(tag, txn):
            log.append((tag, txn.snapshot.snapshot_id, txn.read("acct")))

        observe("long@t0", long_reader)
        commit_value(store, "acct", 80)
        s1 = store.begin()
        observe("short#1", s1)
        observe("long@t1", long_reader)
        commit_value(store, "acct", 60)
        s2 = store.begin()
        observe("short#2", s2)
        observe("long@t2", long_reader)

        self.assertEqual(
            [row[2] for row in log],
            [100, 80, 100, 60, 100],
        )
        for txn in (long_reader, s1, s2):
            txn.abort()


class GCBoundaryTest(unittest.TestCase):
    def test_oldest_snapshot_pins_its_boundary(self):
        store = MVCCStore()
        commit_value(store, "k", "v1")
        old_snap_txn = store.begin()  # boundary pins v1
        commit_value(store, "k", "v2")
        commit_value(store, "k", "v3")

        removed = store.gc()
        # v1 is pinned by the old snapshot, v3 by future snapshots;
        # v2 is needed by nobody (invisible to the old snapshot and
        # superseded for all newer ones), so exactly v2 is collected.
        self.assertEqual(removed, 1)
        self.assertEqual([v.value for v in store.version_history("k")], ["v1", "v3"])
        self.assertEqual(old_snap_txn.read("k"), "v1")
        old_snap_txn.abort()

    def test_gc_collects_after_oldest_snapshot_closes(self):
        store = MVCCStore()
        commit_value(store, "k", "v1")
        old_snap_txn = store.begin()
        commit_value(store, "k", "v2")
        commit_value(store, "k", "v3")
        newer_txn = store.begin()  # boundary at v3

        old_snap_txn.abort()
        removed = store.gc()
        self.assertEqual(removed, 2)  # v1, v2 collected; v3 pinned by newer
        self.assertEqual([v.value for v in store.version_history("k")], ["v3"])
        self.assertEqual(newer_txn.read("k"), "v3")
        newer_txn.abort()

    def test_gc_with_no_snapshots_keeps_only_latest(self):
        store = MVCCStore()
        for v in ("v1", "v2", "v3"):
            commit_value(store, "k", v)
        removed = store.gc()
        self.assertEqual(removed, 2)
        self.assertEqual([v.value for v in store.version_history("k")], ["v3"])

    def test_gc_boundary_is_oldest_of_several_snapshots(self):
        store = MVCCStore()
        commit_value(store, "k", "v1")
        txn_a = store.begin()  # boundary v1
        commit_value(store, "k", "v2")
        txn_b = store.begin()  # boundary v2
        commit_value(store, "k", "v3")

        store.gc()
        self.assertEqual(len(store.version_history("k")), 3)  # pinned by txn_a
        txn_a.abort()
        store.gc()
        self.assertEqual(  # pinned by txn_b: v2 kept, v1 gone
            [v.value for v in store.version_history("k")], ["v2", "v3"]
        )
        txn_b.abort()
        store.gc()
        self.assertEqual([v.value for v in store.version_history("k")], ["v3"])

    def test_gc_preserves_tombstone_needed_by_snapshot(self):
        store = MVCCStore()
        commit_value(store, "k", "v1")
        txn = store.begin()
        deleter = store.begin()
        deleter.delete("k")
        deleter.commit()
        store.gc()
        self.assertEqual(txn.read("k"), "v1")  # old version still pinned
        txn.abort()
        store.gc()
        self.assertIsNone(store.begin().read("k"))


class SnapshotLifecycleTest(unittest.TestCase):
    def test_begin_registers_and_close_releases(self):
        store = MVCCStore()
        self.assertEqual(store.active_snapshots(), [])
        txn = store.begin()
        self.assertEqual(len(store.active_snapshots()), 1)
        txn.abort()
        self.assertEqual(store.active_snapshots(), [])

    def test_commit_releases_snapshot(self):
        store = MVCCStore()
        txn = store.begin()
        txn.write("k", "v")
        txn.commit()
        self.assertEqual(store.active_snapshots(), [])

    def test_use_after_close_raises(self):
        store = MVCCStore()
        txn = store.begin()
        txn.commit()
        with self.assertRaises(TransactionClosedError):
            txn.read("k")
        with self.assertRaises(TransactionClosedError):
            txn.write("k", "v")

    def test_double_abort_is_idempotent(self):
        store = MVCCStore()
        txn = store.begin()
        txn.abort()
        txn.abort()


class SnapshotExpiryTest(unittest.TestCase):
    def test_expired_snapshot_read_raises(self):
        store = MVCCStore()
        commit_value(store, "k", "v1")
        txn = store.begin(ttl=2)
        commit_value(store, "k", "v2")
        commit_value(store, "k", "v3")
        self.assertEqual(txn.read("k"), "v1")  # age 2, still valid
        commit_value(store, "k", "v4")
        with self.assertRaises(SnapshotExpiredError):
            txn.read("k")

    def test_expired_snapshot_cannot_commit(self):
        store = MVCCStore()
        txn = store.begin(ttl=0)
        txn.write("k", "v")
        commit_value(store, "other", 1)
        with self.assertRaises(SnapshotExpiredError):
            txn.commit()

    def test_expired_snapshot_stops_pinning_gc(self):
        store = MVCCStore()
        commit_value(store, "k", "v1")
        txn = store.begin(ttl=1)
        commit_value(store, "k", "v2")
        commit_value(store, "k", "v3")  # snapshot now expired
        removed = store.gc()
        self.assertEqual(removed, 2)
        self.assertEqual([v.value for v in store.version_history("k")], ["v3"])
        self.assertEqual(store.active_snapshots(), [])

    def test_unexpired_snapshots_unaffected_by_others_expiring(self):
        store = MVCCStore()
        commit_value(store, "k", "v1")
        short_lived = store.begin(ttl=0)
        long_lived = store.begin(ttl=100)
        commit_value(store, "k", "v2")
        store.gc()
        self.assertEqual(long_lived.read("k"), "v1")
        with self.assertRaises(SnapshotExpiredError):
            short_lived.read("k")
        long_lived.abort()


class EdgeCaseTest(unittest.TestCase):
    def test_delete_of_missing_key_is_noop(self):
        store = MVCCStore()
        txn = store.begin()
        txn.delete("ghost")
        txn.commit()
        self.assertIsNone(store.begin().read("ghost"))

    def test_many_keys_gc_only_touches_garbage(self):
        store = MVCCStore()
        commit_value(store, "hot", "h1")
        pin = store.begin()
        commit_value(store, "hot", "h2")
        commit_value(store, "cold", "c1")
        commit_value(store, "cold", "c2")
        store.gc()
        self.assertEqual(len(store.version_history("hot")), 2)  # pinned
        self.assertEqual(len(store.version_history("cold")), 1)  # collected
        pin.abort()

    def test_gc_on_empty_store(self):
        store = MVCCStore()
        self.assertEqual(store.gc(), 0)

    def test_snapshot_boundary_matches_commit_sequence(self):
        store = MVCCStore()
        commit_value(store, "k", "v1")
        commit_value(store, "k", "v2")
        txn = store.begin()
        self.assertEqual(txn.snapshot.boundary, 2)
        txn.abort()


if __name__ == "__main__":
    unittest.main()
