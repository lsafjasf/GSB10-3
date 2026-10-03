import threading
import time
import unittest

from fair_semaphore import (
    FairSemaphore,
    FairSemaphoreCancelled,
    FairSemaphoreTimeout,
)


class FairSemaphoreTests(unittest.TestCase):
    def start_waiter(self, semaphore, label, target, *args):
        thread = threading.Thread(target=target, args=(label, *args), daemon=True)
        thread.start()
        self.wait_for_queue_labels(semaphore, label)
        return thread

    def wait_for_queue_labels(self, semaphore, *labels):
        deadline = time.monotonic() + 2
        snapshot = []
        while time.monotonic() < deadline:
            snapshot = semaphore.queue_snapshot()
            actual = [entry["label"] for entry in snapshot]
            if all(label in actual for label in labels):
                return snapshot
            time.sleep(0.001)
        self.fail(f"waiters did not enter queue: expected {labels}, got {snapshot}")

    def test_no_contention_and_boundaries(self):
        semaphore = FairSemaphore()
        semaphore.acquire(label="main")
        self.assertEqual(semaphore.queue_snapshot(), [])
        semaphore.release()

        semaphore.acquire(timeout=0, label="main")
        semaphore.release()
        with self.assertRaises(RuntimeError):
            semaphore.release()

        empty = FairSemaphore(0)
        with self.assertRaises(FairSemaphoreTimeout):
            empty.acquire(timeout=0, label="nonblocking")
        self.assertEqual(empty.queue_snapshot(), [])

        with self.assertRaises(ValueError):
            FairSemaphore(-1)
        with self.assertRaises(ValueError):
            semaphore.acquire(timeout=-0.01)

        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(FairSemaphoreCancelled):
            semaphore.acquire(cancel_event=cancelled, label="already-cancelled")
        semaphore.acquire(label="main")
        semaphore.release()

    def test_fifo_order_under_high_contention(self):
        count = 50
        semaphore = FairSemaphore()
        semaphore.acquire(label="main")
        acquired = []
        acquired_lock = threading.Lock()
        threads = []

        def worker(label):
            semaphore.acquire(label=label)
            with acquired_lock:
                acquired.append(label)
            semaphore.release()

        expected = [f"worker-{index:02d}" for index in range(count)]
        for label in expected:
            threads.append(self.start_waiter(semaphore, label, worker))

        snapshot = semaphore.queue_snapshot()
        self.assertEqual([entry["label"] for entry in snapshot], expected)
        self.assertEqual([entry["ticket"] for entry in snapshot], list(range(count)))
        self.assertTrue(all(entry["state"] == "waiting" for entry in snapshot))

        semaphore.release()
        for thread in threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())

        self.assertEqual(acquired, expected)
        self.assertEqual(semaphore.queue_snapshot(), [])

    def test_release_hands_permit_to_head_not_newcomer(self):
        semaphore = FairSemaphore()
        semaphore.acquire(label="main")
        acquired = []
        acquired_lock = threading.Lock()
        old_acquired = threading.Event()
        allow_old_release = threading.Event()

        def worker(label, acquired_event=None, release_gate=None):
            semaphore.acquire(label=label)
            with acquired_lock:
                acquired.append(label)
            if acquired_event is not None:
                acquired_event.set()
            if release_gate is not None:
                self.assertTrue(release_gate.wait(timeout=2))
            semaphore.release()

        old_thread = self.start_waiter(
            semaphore,
            "old-head",
            worker,
            old_acquired,
            allow_old_release,
        )
        new_thread = self.start_waiter(semaphore, "newcomer", worker)
        self.assertEqual(
            [entry["label"] for entry in semaphore.queue_snapshot()],
            ["old-head", "newcomer"],
        )

        semaphore.release()
        self.assertTrue(old_acquired.wait(timeout=2))
        self.assertEqual(acquired, ["old-head"])
        self.assertEqual(
            [entry["label"] for entry in semaphore.queue_snapshot()],
            ["newcomer"],
        )

        allow_old_release.set()
        old_thread.join(timeout=2)
        self.assertFalse(old_thread.is_alive())

        new_thread.join(timeout=2)
        self.assertFalse(new_thread.is_alive())
        self.assertEqual(acquired, ["old-head", "newcomer"])
        self.assertEqual(semaphore.queue_snapshot(), [])

    def test_timeout_removes_waiter_and_preserves_relative_order(self):
        semaphore = FairSemaphore()
        semaphore.acquire(label="main")
        acquired = []
        errors = {}
        lock = threading.Lock()

        def worker(label, timeout=None):
            try:
                semaphore.acquire(timeout=timeout, label=label)
            except FairSemaphoreTimeout as exc:
                with lock:
                    errors[label] = exc
            else:
                with lock:
                    acquired.append(label)
                semaphore.release()

        first = self.start_waiter(semaphore, "A", worker)
        timed_out = self.start_waiter(semaphore, "B", worker, 0.1)
        third = self.start_waiter(semaphore, "C", worker)

        timed_out.join(timeout=2)
        self.assertFalse(timed_out.is_alive())
        self.assertIsInstance(errors.get("B"), FairSemaphoreTimeout)

        snapshot = semaphore.queue_snapshot()
        self.assertEqual([entry["label"] for entry in snapshot], ["A", "C"])
        self.assertEqual([entry["ticket"] for entry in snapshot], [0, 2])
        self.assertTrue(all(entry["state"] == "waiting" for entry in snapshot))

        semaphore.release()
        first.join(timeout=2)
        third.join(timeout=2)
        self.assertFalse(first.is_alive())
        self.assertFalse(third.is_alive())
        self.assertEqual(acquired, ["A", "C"])
        self.assertEqual(semaphore.queue_snapshot(), [])

    def test_cancelled_waiter_is_removed_and_relative_order_survives(self):
        semaphore = FairSemaphore()
        semaphore.acquire(label="main")
        acquired = []
        errors = {}
        lock = threading.Lock()
        cancel_b = threading.Event()

        def worker(label, cancel_event=None):
            try:
                semaphore.acquire(cancel_event=cancel_event, label=label)
            except FairSemaphoreCancelled as exc:
                with lock:
                    errors[label] = exc
            else:
                with lock:
                    acquired.append(label)
                semaphore.release()

        first = self.start_waiter(semaphore, "A", worker)
        cancelled_thread = self.start_waiter(semaphore, "B", worker, cancel_b)
        third = self.start_waiter(semaphore, "C", worker)

        cancel_b.set()
        cancelled_thread.join(timeout=2)
        self.assertFalse(cancelled_thread.is_alive())
        self.assertIsInstance(errors.get("B"), FairSemaphoreCancelled)

        snapshot = semaphore.queue_snapshot()
        self.assertEqual([entry["label"] for entry in snapshot], ["A", "C"])
        self.assertEqual([entry["ticket"] for entry in snapshot], [0, 2])

        semaphore.release()
        first.join(timeout=2)
        third.join(timeout=2)
        self.assertFalse(first.is_alive())
        self.assertFalse(third.is_alive())
        self.assertEqual(acquired, ["A", "C"])
        self.assertEqual(semaphore.queue_snapshot(), [])

    def test_cancelled_head_cannot_block_or_consume_handoff(self):
        semaphore = FairSemaphore()
        semaphore.acquire(label="main")
        acquired = []
        errors = {}
        lock = threading.Lock()
        cancel_head = threading.Event()

        def worker(label, cancel_event=None):
            try:
                semaphore.acquire(cancel_event=cancel_event, label=label)
            except FairSemaphoreCancelled as exc:
                with lock:
                    errors[label] = exc
            else:
                with lock:
                    acquired.append(label)
                semaphore.release()

        head = self.start_waiter(semaphore, "cancelled-head", worker, cancel_head)
        follower = self.start_waiter(semaphore, "follower", worker)
        cancel_head.set()
        semaphore.release()

        head.join(timeout=2)
        follower.join(timeout=2)
        self.assertFalse(head.is_alive())
        self.assertFalse(follower.is_alive())
        self.assertIsInstance(errors.get("cancelled-head"), FairSemaphoreCancelled)
        self.assertEqual(acquired, ["follower"])
        self.assertEqual(semaphore.queue_snapshot(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
