"""Self-tests for bulkhead_pool (stdlib unittest only).

Run:  python3 -m unittest -v   (or)  python3 test_bulkhead_pool.py
"""

import threading
import time
import unittest

from bulkhead_pool import BulkheadPool, RejectedError


def wait_for(cond, timeout=5.0, interval=0.005):
    """Poll ``cond`` until true or the deadline passes."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(interval)
    return cond()


class ConcurrencyProbe:
    """Tracks current/peak concurrency of submitted tasks."""

    def __init__(self):
        self._lock = threading.Lock()
        self.current = 0
        self.peak = 0

    def __call__(self, gate, tag=None, results=None):
        with self._lock:
            self.current += 1
            self.peak = max(self.peak, self.current)
        try:
            gate.wait(timeout=5.0)
            return tag
        finally:
            with self._lock:
                self.current -= 1


class NormalConcurrencyTest(unittest.TestCase):
    """Two categories run side by side, each within its reservation."""

    def test_two_categories_full_pool_concurrency(self):
        pool = BulkheadPool(total_slots=4)
        pool.register_category("fast", reserved=2, queue_limit=10)
        pool.register_category("slow", reserved=2, queue_limit=10)
        gate = threading.Event()
        probe = ConcurrencyProbe()

        futures = [pool.submit(name, probe, gate, tag=f"{name}{i}")
                   for name in ("fast", "slow") for i in (0, 1)]

        self.assertTrue(wait_for(lambda: probe.peak == 4),
                        "all 4 tasks should run concurrently")
        gate.set()
        results = sorted(f.result(timeout=5) for f in futures)
        self.assertEqual(results, ["fast0", "fast1", "slow0", "slow1"])

        self.assertTrue(wait_for(lambda: pool.free_slots() == 4))
        for name in ("fast", "slow"):
            st = pool.stats(name)
            self.assertEqual((st.running, st.queued, st.borrowed), (0, 0, 0))
            self.assertEqual(st.completed, 2)
            self.assertEqual(st.rejected, 0)
        pool.shutdown()


class SingleCategorySaturationTest(unittest.TestCase):
    """One category fills its slots + queue; excess is rejected and counted."""

    def test_saturation_and_rejection_count(self):
        pool = BulkheadPool(total_slots=2)
        pool.register_category("hot", reserved=2, queue_limit=1)
        gate = threading.Event()

        f1 = pool.submit("hot", gate.wait, 5.0)   # running
        f2 = pool.submit("hot", gate.wait, 5.0)   # running
        f3 = pool.submit("hot", gate.wait, 5.0)   # queued (limit = 1)

        st = pool.stats("hot")
        self.assertEqual((st.running, st.queued), (2, 1))

        # queue full -> reject the newest, count it
        with self.assertRaises(RejectedError):
            pool.submit("hot", gate.wait, 5.0)
        with self.assertRaises(RejectedError):
            pool.submit("hot", gate.wait, 5.0)
        self.assertEqual(pool.stats("hot").rejected, 2)

        gate.set()
        for f in (f1, f2, f3):
            f.result(timeout=5)
        self.assertTrue(wait_for(lambda: pool.stats("hot").completed == 3))
        st = pool.stats("hot")
        self.assertEqual(st.completed, 3)
        self.assertEqual((st.running, st.queued), (0, 0))
        self.assertEqual(pool.free_slots(), 2)
        pool.shutdown()

    def test_zero_queue_limit_rejects_immediately(self):
        pool = BulkheadPool(total_slots=1)
        pool.register_category("noq", reserved=1, queue_limit=0)
        gate = threading.Event()
        f = pool.submit("noq", gate.wait, 5.0)
        with self.assertRaises(RejectedError):
            pool.submit("noq", gate.wait, 5.0)
        self.assertEqual(pool.stats("noq").rejected, 1)
        gate.set()
        f.result(timeout=5)
        pool.shutdown()


class BorrowAndReclaimTest(unittest.TestCase):
    """Idle slots are borrowable; the owner reclaims them when it has demand.

    Timeline (total = 1 slot, owner reserves it, borrower reserves none):

      t0  borrower b1 occupies the only slot (borrowed)
      t1  owner o1 arrives  -> queued (slot is busy)
      t2  borrower b2 arrives -> queued behind o1
      t3  b1 finishes -> slot is reclaimed by the OWNER, o1 runs first
      t4  o1 finishes -> slot lent out again, b2 runs (borrowed)
    """

    def test_borrow_then_reclaim_order(self):
        pool = BulkheadPool(total_slots=1)
        pool.register_category("owner", reserved=1, queue_limit=10)
        pool.register_category("borrower", reserved=0, queue_limit=10)

        gate_b1 = threading.Event()
        gate_o1 = threading.Event()
        started = []          # ordered record of task starts
        done = threading.Event()

        def record(tag, gate=None):
            started.append(tag)
            if gate is not None:
                gate.wait(timeout=5.0)
            return tag

        b1 = pool.submit("borrower", record, "b1", gate_b1)
        self.assertTrue(wait_for(lambda: pool.stats("borrower").borrowed == 1))
        self.assertEqual(pool.free_slots(), 0)

        o1 = pool.submit("owner", record, "o1", gate_o1)   # must queue
        b2 = pool.submit("borrower", record, "b2")          # queues behind o1
        self.assertEqual(pool.stats("owner").queued, 1)
        self.assertEqual(pool.stats("borrower").queued, 1)

        # Reclaim: when b1 frees the slot, the owner gets it back even
        # though the borrower also has queued demand.
        gate_b1.set()
        self.assertTrue(wait_for(lambda: started == ["b1", "o1"]),
                        f"owner must run before queued borrower, got {started}")
        self.assertEqual(pool.stats("owner").running, 1)
        self.assertEqual(pool.stats("owner").borrowed, 0)   # own slot, not borrowed
        self.assertEqual(pool.stats("borrower").running, 0)  # b2 still waiting

        gate_o1.set()
        self.assertEqual(o1.result(timeout=5), "o1")
        self.assertEqual(b1.result(timeout=5), "b1")
        self.assertEqual(b2.result(timeout=5), "b2")
        self.assertEqual(started, ["b1", "o1", "b2"])

        # Everything returned: no leaked or still-borrowed slots.
        self.assertTrue(wait_for(lambda: pool.free_slots() == 1))
        self.assertEqual(pool.stats("borrower").borrowed, 0)
        pool.shutdown()

    def test_borrow_expands_beyond_reservation(self):
        pool = BulkheadPool(total_slots=3)
        pool.register_category("idle_cat", reserved=2, queue_limit=5)
        pool.register_category("busy", reserved=1, queue_limit=5)
        gate = threading.Event()
        probe = ConcurrencyProbe()

        futures = [pool.submit("busy", probe, gate) for _ in range(3)]
        self.assertTrue(wait_for(lambda: probe.peak == 3),
                        "busy category should borrow 2 idle slots")
        st = pool.stats("busy")
        self.assertEqual(st.running, 3)
        self.assertEqual(st.borrowed, 2)   # 1 own + 2 borrowed
        gate.set()
        for f in futures:
            f.result(timeout=5)
        self.assertTrue(wait_for(lambda: pool.stats("busy").completed == 3))
        self.assertEqual(pool.stats("busy").borrowed, 0)
        self.assertEqual(pool.free_slots(), 3)
        pool.shutdown()


class ExceptionSafetyTest(unittest.TestCase):
    """A raising task must not leak its slot."""

    def test_slot_released_after_exception(self):
        pool = BulkheadPool(total_slots=2)
        pool.register_category("flaky", reserved=2, queue_limit=5)

        def boom():
            raise ValueError("task exploded")

        f = pool.submit("flaky", boom)
        with self.assertRaises(ValueError):
            f.result(timeout=5)

        self.assertTrue(wait_for(lambda: pool.stats("flaky").failed == 1))
        # The slot is back: pool-wide and per-category views agree.
        self.assertEqual(pool.free_slots(), 2)
        self.assertEqual(pool.available_for("flaky"), 2)
        st = pool.stats("flaky")
        self.assertEqual((st.running, st.queued, st.borrowed), (0, 0, 0))

        # Pool still fully usable afterwards.
        ok = pool.submit("flaky", lambda: 42)
        self.assertEqual(ok.result(timeout=5), 42)
        self.assertTrue(wait_for(lambda: pool.stats("flaky").completed == 1))
        self.assertEqual(pool.free_slots(), 2)
        pool.shutdown()

    def test_borrowed_slot_released_after_exception(self):
        pool = BulkheadPool(total_slots=1)
        pool.register_category("owner", reserved=1, queue_limit=5)
        pool.register_category("guest", reserved=0, queue_limit=5)

        f = pool.submit("guest", lambda: 1 / 0)   # runs on a borrowed slot
        with self.assertRaises(ZeroDivisionError):
            f.result(timeout=5)

        self.assertTrue(wait_for(lambda: pool.stats("guest").failed == 1))
        self.assertEqual(pool.stats("guest").borrowed, 0)
        self.assertEqual(pool.free_slots(), 1)
        # Owner can still claim its slot immediately.
        self.assertEqual(pool.submit("owner", lambda: "ok").result(timeout=5), "ok")
        pool.shutdown()


class EdgeCaseTest(unittest.TestCase):
    def test_registration_validation(self):
        pool = BulkheadPool(total_slots=2)
        with self.assertRaises(ValueError):
            BulkheadPool(0)
        with self.assertRaises(ValueError):
            pool.register_category("neg", reserved=-1, queue_limit=1)
        with self.assertRaises(ValueError):
            pool.register_category("badq", reserved=1, queue_limit=-1)
        pool.register_category("a", reserved=2, queue_limit=1)
        with self.assertRaises(ValueError):
            pool.register_category("a", reserved=0, queue_limit=1)   # duplicate
        with self.assertRaises(ValueError):
            pool.register_category("b", reserved=1, queue_limit=1)   # over budget
        pool.register_category("c", reserved=0, queue_limit=0)       # zero is fine
        pool.shutdown()

    def test_unknown_category(self):
        pool = BulkheadPool(total_slots=1)
        with self.assertRaises(KeyError):
            pool.submit("ghost", lambda: None)
        pool.shutdown()

    def test_shutdown_rejects_new_work_and_drains(self):
        pool = BulkheadPool(total_slots=1)
        pool.register_category("x", reserved=1, queue_limit=5)
        gate = threading.Event()
        f1 = pool.submit("x", gate.wait, 5.0)
        f2 = pool.submit("x", lambda: "queued")
        gate.set()
        pool.shutdown(wait=True)   # must drain running + queued tasks
        self.assertEqual(f2.result(timeout=5), "queued")
        self.assertEqual(pool.stats("x").completed, 2)
        with self.assertRaises(RuntimeError):
            pool.submit("x", lambda: None)
        f1.result(timeout=5)

    def test_queue_limit_boundary_exact(self):
        pool = BulkheadPool(total_slots=1)
        pool.register_category("exact", reserved=1, queue_limit=2)
        gate = threading.Event()
        kept = [pool.submit("exact", gate.wait, 5.0) for _ in range(3)]  # 1 run + 2 queued
        self.assertEqual(pool.stats("exact").queued, 2)
        with self.assertRaises(RejectedError):
            pool.submit("exact", gate.wait, 5.0)   # 4th exceeds the limit
        self.assertEqual(pool.stats("exact").rejected, 1)
        gate.set()
        for f in kept:
            f.result(timeout=5)
        self.assertTrue(wait_for(lambda: pool.stats("exact").completed == 3))
        pool.shutdown()


if __name__ == "__main__":
    unittest.main(verbosity=2)
