"""Self-tests for FairSemaphore: order assertions, queue snapshots,
edge cases.  Run:  python3 test_fair_semaphore.py  (or: python3 -m unittest -v)
"""

import threading
import time
import unittest

from fair_semaphore import FairSemaphore


def enqueue_in_order(sem, names, results, timeout=None, cancels=None,
                     barrier=None):
    """Spawn one thread per name; thread i only calls acquire() once the
    queue length is exactly i, so arrival order == `names` order."""
    threads = []
    for i, nm in enumerate(names):
        def run(i=i, nm=nm):
            if barrier is not None:
                barrier.wait()
            while len(sem) != i:          # enforce deterministic arrival
                time.sleep(0.001)
            kw = {"name": nm}
            if timeout is not None:
                kw["timeout"] = timeout[i]
            if cancels is not None:
                kw["cancel"] = cancels[i]
            ok = sem.acquire(**kw)
            results.append((nm, ok, time.monotonic()))
        t = threading.Thread(target=run, name=nm)
        t.start()
        threads.append(t)
    return threads


class TestNoContention(unittest.TestCase):
    def test_fast_path(self):
        sem = FairSemaphore(2)
        self.assertTrue(sem.acquire())          # immediate
        self.assertEqual(sem.value, 1)
        self.assertTrue(sem.acquire(timeout=0))  # zero-timeout fast path
        self.assertEqual(sem.value, 0)
        sem.release()
        sem.release()
        self.assertEqual(sem.value, 2)
        self.assertEqual(sem.snapshot(), [])

    def test_zero_timeout_when_busy(self):
        sem = FairSemaphore(1)
        self.assertTrue(sem.acquire())
        t0 = time.monotonic()
        self.assertFalse(sem.acquire(timeout=0))   # must not block
        self.assertLess(time.monotonic() - t0, 0.1)

    def test_over_release_raises(self):
        sem = FairSemaphore(1)
        sem.acquire()
        sem.release()
        with self.assertRaises(ValueError):
            sem.release()

    def test_invalid_initial_value(self):
        with self.assertRaises(ValueError):
            FairSemaphore(-1)


class TestFifoOrder(unittest.TestCase):
    def test_wakeup_order_equals_arrival_order(self):
        N = 8
        sem = FairSemaphore(1)
        sem.acquire()                            # hold the only permit
        results = []
        threads = enqueue_in_order(sem, ["W%d" % i for i in range(N)],
                                   results)
        time.sleep(0.1)
        # Queue snapshot: head first, arrival order.
        self.assertEqual(sem.snapshot(), ["W%d" % i for i in range(N)])
        for _ in range(N):                       # each release wakes exactly
            sem.release()                        # the current head
            time.sleep(0.02)
        for t in threads:
            t.join()
        got = [nm for nm, ok, _ in results]
        # ORDER ASSERTION: wakeup order == arrival order
        self.assertEqual(got, ["W%d" % i for i in range(N)])
        self.assertTrue(all(ok for _, ok, _ in results))
        sem.release()                            # restore
        self.assertEqual(sem.value, 1)


class TestHandoffNoSteal(unittest.TestCase):
    def test_newcomer_cannot_steal_handoff(self):
        sem = FairSemaphore(1)
        sem.acquire()
        results = []
        # One patient waiter queued...
        waiter = enqueue_in_order(sem, ["patient"], results)[0]
        time.sleep(0.1)
        self.assertEqual(sem.snapshot(), ["patient"])
        # ...and a crowd of thieves arriving right after the release.
        thieves_done = []
        stop = threading.Event()

        def thief(i):
            while not stop.is_set():
                if sem.acquire(timeout=0.01, name="thief-%d" % i):
                    thieves_done.append(i)
                    sem.release()
        thieves = [threading.Thread(target=thief, args=(i,)) for i in range(6)]
        for t in thieves:
            t.start()
        sem.release()                            # handoff must go to patient
        waiter.join(timeout=2)
        stop.set()
        for t in thieves:
            t.join()
        # The queued waiter got the permit before any newcomer.
        self.assertEqual(results[0][0], "patient")
        self.assertTrue(results[0][1])
        self.assertEqual(len(thieves_done), 0)   # nobody stole the handoff
        sem.release()
        self.assertEqual(sem.value, 1)


class TestTimeoutRemoval(unittest.TestCase):
    def test_timeout_dequeues_and_preserves_order(self):
        sem = FairSemaphore(1)
        sem.acquire()
        names = ["A", "B", "C", "D", "E"]
        timeouts = [5.0, 0.15, 5.0, 0.15, 5.0]   # B and D will time out
        results = []
        threads = enqueue_in_order(sem, names, results, timeout=timeouts)
        time.sleep(0.1)
        snap_before = sem.snapshot()
        print("\n[snapshot] before timeouts:", snap_before)
        self.assertEqual(snap_before, ["A", "B", "C", "D", "E"])
        time.sleep(0.4)                          # let B and D expire
        snap_after = sem.snapshot()
        print("[snapshot] after B,D timed out:", snap_after)
        # QUEUE SNAPSHOT ASSERTION: B,D removed; A,C,E keep relative order
        self.assertEqual(snap_after, ["A", "C", "E"])
        for _ in range(3):                       # survivors served in order
            sem.release()
            time.sleep(0.02)
        for t in threads:
            t.join()
        ok_names = [nm for nm, ok, _ in results if ok]
        fail_names = sorted(nm for nm, ok, _ in results if not ok)
        self.assertEqual(ok_names, ["A", "C", "E"])   # FIFO among survivors
        self.assertEqual(fail_names, ["B", "D"])
        sem.release()
        self.assertEqual(sem.value, 1)
        self.assertEqual(sem.snapshot(), [])


class TestCancellation(unittest.TestCase):
    def test_cancelled_waiter_dequeues(self):
        sem = FairSemaphore(1)
        sem.acquire()
        names = ["A", "B", "C", "D"]
        cancels = [threading.Event() for _ in names]
        results = []
        threads = enqueue_in_order(sem, names, results, cancels=cancels)
        time.sleep(0.1)
        self.assertEqual(sem.snapshot(), ["A", "B", "C", "D"])
        cancels[2].set()                         # cancel C (mid-queue)
        time.sleep(0.2)
        snap = sem.snapshot()
        print("[snapshot] after C cancelled:", snap)
        self.assertEqual(snap, ["A", "B", "D"])  # C gone, order preserved
        for _ in range(3):
            sem.release()
            time.sleep(0.02)
        for t in threads:
            t.join()
        ok_names = [nm for nm, ok, _ in results if ok]
        self.assertEqual(ok_names, ["A", "B", "D"])
        self.assertIn(("C", False), [(nm, ok) for nm, ok, _ in results])
        sem.release()
        self.assertEqual(sem.value, 1)


class TestHighContention(unittest.TestCase):
    def test_no_permit_leak_and_bounded_holders(self):
        PERMITS, THREADS, ITERS = 3, 20, 50
        sem = FairSemaphore(PERMITS)
        holders = 0
        max_holders = 0
        lock = threading.Lock()

        def worker():
            nonlocal holders, max_holders
            for _ in range(ITERS):
                self.assertTrue(sem.acquire(timeout=5.0))
                with lock:
                    holders += 1
                    max_holders = max(max_holders, holders)
                time.sleep(0.0005)
                with lock:
                    holders -= 1
                sem.release()

        threads = [threading.Thread(target=worker) for _ in range(THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertLessEqual(max_holders, PERMITS)   # never over-admitted
        self.assertEqual(sem.value, PERMITS)         # no permit leaked
        self.assertEqual(sem.snapshot(), [])         # no waiter lost

    def test_timeout_release_race_no_leak(self):
        # Hammer the timeout/release race: a waiter that times out exactly
        # when a permit is being handed to it must re-handoff, not leak.
        sem = FairSemaphore(1)
        for _ in range(300):
            sem.acquire()
            got = []

            def waiter():
                got.append(sem.acquire(timeout=0.005, name="racer"))
            t = threading.Thread(target=waiter)
            t.start()
            while len(sem) != 1:
                time.sleep(0.0005)
            time.sleep(0.004)                  # release near the deadline
            sem.release()
            t.join()
            if got == [True]:                  # permit consumed -> give back
                sem.release()
            self.assertEqual(sem.value, 1)     # never 0 (leak) or 2 (dup)
            self.assertEqual(sem.snapshot(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
