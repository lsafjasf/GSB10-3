"""Self-tests for bloat detection and online rebuild.

Run:  python3 -m unittest -v test_index_bloat
"""

import os
import random
import shutil
import tempfile
import threading
import time
import unittest

from index_bloat import (
    LogIndex,
    OnlineRebuilder,
    RebuildInterrupted,
    measure,
)
from index_bloat.metrics import MIN_REBUILD_BYTES, REBUILD_EFFICIENCY_THRESHOLD


def k(i):
    return b"key-%06d" % i


def v(i, n=64):
    return b"v%06d-" % i + bytes([i % 251]) * (n - 8)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="idxbloat-")
        self.path = os.path.join(self.dir, "index.log")
        self.idx = LogIndex(self.path)

    def tearDown(self):
        self.idx.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def fill(self, n, size=64):
        for i in range(n):
            self.idx.put(k(i), v(i, size))

    def assert_model_matches(self, model):
        for key, val in model.items():
            self.assertEqual(self.idx.get(key), val, "key %r" % key)


class TestBloatMetric(Base):
    def test_slight_bloat_does_not_trigger(self):
        # 2000 live keys, overwrite 10% of them once -> ~5% garbage
        self.fill(2000)
        for i in range(0, 2000, 10):
            self.idx.put(k(i), v(i, 64))
        r = measure(self.idx)
        self.assertGreater(r.efficiency, REBUILD_EFFICIENCY_THRESHOLD)
        self.assertFalse(r.should_rebuild)

    def test_severe_bloat_triggers(self):
        # rewrite every key 8x and delete half -> efficiency well under 0.5
        self.fill(2000)
        for _ in range(8):
            for i in range(2000):
                self.idx.put(k(i), v(i, 64))
        for i in range(0, 2000, 2):
            self.idx.delete(k(i))
        r = measure(self.idx)
        self.assertLess(r.efficiency, REBUILD_EFFICIENCY_THRESHOLD)
        self.assertTrue(r.should_rebuild)

    def test_threshold_boundary_is_strict(self):
        # exactly 50% efficiency must NOT trigger (strict <)
        self.fill(1000)
        for i in range(1000):
            self.idx.put(k(i), v(i, 64))  # same size -> exactly half live
        r = measure(self.idx)
        self.assertAlmostEqual(r.efficiency, 0.5, places=6)
        self.assertFalse(r.should_rebuild)

    def test_tiny_index_never_triggers(self):
        # heavy relative bloat but far below the absolute size floor
        self.fill(10)
        for _ in range(5):
            for i in range(10):
                self.idx.put(k(i), v(i, 16))
        r = measure(self.idx)
        self.assertLess(r.total_bytes, MIN_REBUILD_BYTES)
        self.assertFalse(r.should_rebuild)


class TestOnlineRebuild(Base):
    def test_rebuild_reclaims_space_and_preserves_data(self):
        self.fill(2000)
        for _ in range(8):
            for i in range(2000):
                self.idx.put(k(i), v(i, 64))
        for i in range(0, 2000, 2):
            self.idx.delete(k(i))
        before = measure(self.idx)
        self.assertTrue(before.should_rebuild)

        rb = OnlineRebuilder(self.idx).run()
        self.assertEqual(rb.mismatches, 0)
        self.assertGreater(rb.checked, 0)

        after = measure(self.idx)
        self.assertGreater(after.efficiency, 0.99)
        self.assertLess(after.total_bytes, before.total_bytes // 5)
        for i in range(2000):
            expect = None if i % 2 == 0 else v(i, 64)
            self.assertEqual(self.idx.get(k(i)), expect)

    def test_writes_during_rebuild_are_not_lost(self):
        self.fill(2000)
        for _ in range(6):
            for i in range(2000):
                self.idx.put(k(i), v(i, 64))

        model = {k(i): v(i, 64) for i in range(2000)}
        stop = threading.Event()
        errors = []
        rng = random.Random(20261004)

        def writer():
            n = 0
            while not stop.is_set():
                i = rng.randrange(3000)  # overlaps and extends the key space
                if rng.random() < 0.7:
                    val = b"w%d-%d" % (n, i)
                    self.idx.put(k(i), val)
                    model[k(i)] = val
                else:
                    self.idx.delete(k(i))
                    model.pop(k(i), None)
                n += 1

        def reader():
            while not stop.is_set():
                try:
                    self.idx.get(k(rng.randrange(3000)))
                except Exception as e:  # noqa: BLE001
                    errors.append(e)
                    stop.set()

        threads = [threading.Thread(target=writer)]
        threads += [threading.Thread(target=reader) for _ in range(3)]
        for t in threads:
            t.start()
        # let writes flow, then rebuild while they keep flowing
        time.sleep(0.05)
        OnlineRebuilder(self.idx).run()
        stop.set()
        for t in threads:
            t.join()

        self.assertFalse(errors, "reader errors: %r" % errors[:3])
        self.assert_model_matches(model)

    def test_interrupted_rebuild_leaves_old_index_intact(self):
        self.fill(2000)
        for _ in range(6):
            for i in range(2000):
                self.idx.put(k(i), v(i, 64))
        before_total, _, _ = self.idx.stats()

        for fail_at in ("copy", "catchup", "verify"):
            with self.assertRaises(RebuildInterrupted):
                OnlineRebuilder(self.idx).run(fail_at=fail_at)
            # old index fully functional, file untouched, temp cleaned up
            self.assertEqual(self.idx.stats()[0], before_total)
            self.assertFalse(os.path.exists(self.path + ".rebuild"))
            for i in (0, 999, 1999):
                self.assertEqual(self.idx.get(k(i)), v(i, 64))
            self.idx.put(b"after-crash", b"ok")
            self.assertEqual(self.idx.get(b"after-crash"), b"ok")
            before_total = self.idx.stats()[0]

        # and a retry succeeds
        rb = OnlineRebuilder(self.idx).run()
        self.assertEqual(rb.mismatches, 0)
        self.assertLess(measure(self.idx).total_bytes, before_total // 5)

    def test_atomic_switch_is_invisible_to_readers(self):
        # readers hammering the index must never see a torn/missing state
        self.fill(2000)
        for _ in range(6):
            for i in range(2000):
                self.idx.put(k(i), v(i, 64))
        stop = threading.Event()
        errors = []

        def reader():
            while not stop.is_set():
                try:
                    got = self.idx.get(k(1234))
                    if got != v(1234, 64):
                        errors.append("wrong value: %r" % got)
                        stop.set()
                except Exception as e:  # noqa: BLE001
                    errors.append(repr(e))
                    stop.set()

        threads = [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        OnlineRebuilder(self.idx).run()
        stop.set()
        for t in threads:
            t.join()
        self.assertFalse(errors, errors[:3])


class TestEdgeCases(Base):
    def test_rebuild_empty_index(self):
        rb = OnlineRebuilder(self.idx).run()
        self.assertEqual(rb.mismatches, 0)
        self.assertEqual(self.idx.stats()[0], 0)
        self.idx.put(b"a", b"1")
        self.assertEqual(self.idx.get(b"a"), b"1")

    def test_rebuild_all_deleted(self):
        self.fill(1000)
        for i in range(1000):
            self.idx.delete(k(i))
        r = measure(self.idx)
        self.assertEqual(r.live_bytes, 0)
        self.assertTrue(r.should_rebuild)
        rb = OnlineRebuilder(self.idx).run()
        self.assertEqual(rb.mismatches, 0)
        self.assertEqual(self.idx.stats()[0], 0)
        for i in range(1000):
            self.assertIsNone(self.idx.get(k(i)))

    def test_rebuild_with_no_bloat_is_still_correct(self):
        self.fill(500)
        rb = OnlineRebuilder(self.idx).run()
        self.assertEqual(rb.mismatches, 0)
        for i in range(500):
            self.assertEqual(self.idx.get(k(i)), v(i, 64))

    def test_reopen_after_rebuild(self):
        self.fill(1000)
        for _ in range(4):
            for i in range(1000):
                self.idx.put(k(i), v(i, 64))
        OnlineRebuilder(self.idx).run()
        self.idx.close()
        self.idx = LogIndex(self.path)  # recover from the compacted file
        for i in range(1000):
            self.assertEqual(self.idx.get(k(i)), v(i, 64))
        r = measure(self.idx)
        self.assertGreater(r.efficiency, 0.99)

    def test_delete_missing_key_then_rebuild(self):
        self.fill(100)
        self.idx.delete(b"never-existed")
        OnlineRebuilder(self.idx).run()
        self.assertIsNone(self.idx.get(b"never-existed"))
        self.assertEqual(self.idx.get(k(0)), v(0, 64))


if __name__ == "__main__":
    unittest.main()
