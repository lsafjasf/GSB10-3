"""引用计数正确性、错误检测与边界用例。"""

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rcuref import (
    Domain,
    DoubleReleaseError,
    DrainTimeoutError,
    LifecycleError,
    ObjectReclaimedError,
    RefcountOverflowError,
    RetiredObjectError,
    UseAfterReleaseError,
)


class AcquireReleaseTest(unittest.TestCase):
    def test_acquire_release_is_atomic_and_counts(self):
        dom = Domain()
        obj = dom.register("payload")
        self.assertEqual(obj.refcount, 1)
        refs = [obj.acquire() for _ in range(10)]
        self.assertEqual(obj.refcount, 11)
        for r in refs:
            r.release()
        self.assertEqual(obj.refcount, 1)
        self.assertEqual(obj.state, "ALIVE")

    def test_zero_count_retires_but_does_not_destroy(self):
        destroyed = []
        dom = Domain()
        obj = dom.register("payload", reclaim=lambda o: destroyed.append(o))
        obj.release()
        # 计数归零：进入待回收队列，但绝不立即销毁
        self.assertEqual(obj.state, "RETIRED")
        self.assertEqual(obj.refcount, 0)
        self.assertEqual(destroyed, [])
        self.assertEqual(dom.pending_count, 1)
        # 无读方时宽限期即刻满足，显式回收才销毁
        self.assertEqual(dom.reclaim_ready(), 1)
        self.assertEqual(len(destroyed), 1)
        self.assertEqual(obj.state, "RECLAIMED")

    def test_ref_value_and_context_manager(self):
        dom = Domain()
        obj = dom.register({"k": 1})
        with obj.acquire() as ref:
            self.assertEqual(ref.value, {"k": 1})
        self.assertTrue(ref.released)
        self.assertEqual(obj.refcount, 1)


class ConcurrentAcquireReleaseTest(unittest.TestCase):
    """多线程并发获取/释放：最终计数必须精确回到 1。"""

    def test_concurrent_acquire_release(self):
        dom = Domain()
        obj = dom.register("hot")
        threads_n, loops = 8, 2000
        barrier = threading.Barrier(threads_n)
        errors = []

        def worker():
            try:
                barrier.wait(timeout=10)
                for _ in range(loops):
                    ref = obj.acquire()
                    _ = ref.value
                    ref.release()
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(threads_n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        self.assertEqual(obj.refcount, 1)
        self.assertEqual(obj.state, "ALIVE")

    def test_concurrent_release_to_zero_retires_once(self):
        dom = Domain()
        obj = dom.register("x")
        refs = [obj.acquire() for _ in range(64)]
        obj.release()  # 释放创建引用，剩余 64
        threads = [threading.Thread(target=r.release) for r in refs]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(obj.refcount, 0)
        self.assertEqual(obj.state, "RETIRED")
        self.assertEqual(dom.pending_count, 1)


class LongReaderTest(unittest.TestCase):
    """长时间读方：回收被推迟到读方退出之后。"""

    def test_long_reader_defers_reclaim(self):
        destroyed = []
        dom = Domain()
        obj = dom.register("data", reclaim=lambda o: destroyed.append(o))
        entered = threading.Event()
        leave = threading.Event()

        def reader():
            with dom.read_lock():
                entered.set()
                leave.wait(10)

        t = threading.Thread(target=reader)
        t.start()
        self.assertTrue(entered.wait(5))
        obj.release()
        self.assertEqual(obj.state, "RETIRED")
        # 读方未退出：反复尝试回收都不允许销毁
        for _ in range(5):
            self.assertEqual(dom.reclaim_ready(), 0)
            self.assertEqual(destroyed, [])
            self.assertFalse(obj.is_reclaimed)
        leave.set()
        t.join(5)
        dom.drain(timeout=5)
        self.assertEqual(destroyed, [obj])
        self.assertTrue(obj.is_reclaimed)

    def test_late_reader_cannot_resurrect_retired_object(self):
        dom = Domain()
        obj = dom.register("data")
        obj.release()
        with self.assertRaises(RetiredObjectError):
            obj.acquire()


class OverflowTest(unittest.TestCase):
    def test_overflow_detected_and_count_unchanged(self):
        dom = Domain()
        obj = dom.register("x", max_refcount=3)
        r1 = obj.acquire()
        r2 = obj.acquire()
        self.assertEqual(obj.refcount, 3)
        with self.assertRaises(RefcountOverflowError):
            obj.acquire()
        self.assertEqual(obj.refcount, 3)  # 溢出后计数不变
        r1.release()
        r2.release()
        obj.release()
        self.assertEqual(obj.state, "RETIRED")

    def test_overflow_under_concurrency(self):
        dom = Domain()
        obj = dom.register("x", max_refcount=64)
        stop = threading.Event()
        overflow_seen = []
        unexpected = []

        def worker():
            try:
                while not stop.is_set():
                    held = []
                    try:
                        # 批量获取并持有一小段时间，制造计数打满的竞争
                        for _ in range(16):
                            held.append(obj.acquire())
                        time.sleep(0.001)
                    except RefcountOverflowError:
                        overflow_seen.append(1)
                    finally:
                        for ref in held:
                            ref.release()
            except Exception as exc:  # pragma: no cover
                unexpected.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        time.sleep(1.0)
        stop.set()
        for t in threads:
            t.join(10)
        self.assertEqual(unexpected, [])
        self.assertTrue(overflow_seen, "溢出分支应被触发")
        self.assertLessEqual(obj.refcount, 64)
        obj.release()
        self.assertEqual(obj.refcount, 0)


class DoubleReleaseTest(unittest.TestCase):
    def test_double_release_of_ref(self):
        dom = Domain()
        obj = dom.register("x")
        ref = obj.acquire()
        ref.release()
        with self.assertRaises(DoubleReleaseError):
            ref.release()
        self.assertEqual(obj.refcount, 1)  # 第二次释放未生效

    def test_double_release_of_creation_ref(self):
        dom = Domain()
        obj = dom.register("x")
        obj.release()
        with self.assertRaises(DoubleReleaseError):
            obj.release()

    def test_release_after_reclaim_detected(self):
        dom = Domain()
        obj = dom.register("x")
        ref = obj.acquire()
        obj.release()
        ref.release()
        dom.drain(timeout=5)
        self.assertTrue(obj.is_reclaimed)
        # 已回收对象上的任何额外释放都必须被检出
        with self.assertRaises(DoubleReleaseError):
            with obj._lock:
                obj._decrement_locked()


class UseAfterReleaseTest(unittest.TestCase):
    def test_access_via_released_ref(self):
        dom = Domain()
        obj = dom.register("x")
        ref = obj.acquire()
        ref.release()
        with self.assertRaises(UseAfterReleaseError):
            ref.get()
        with self.assertRaises(UseAfterReleaseError):
            _ = ref.value

    def test_access_after_reclaim(self):
        dom = Domain()
        obj = dom.register("x")
        obj.release()
        dom.drain(timeout=5)
        with self.assertRaises(ObjectReclaimedError):
            _ = obj.payload


class EdgeCaseTest(unittest.TestCase):
    def test_max_refcount_one_allows_no_acquire(self):
        dom = Domain()
        obj = dom.register("x", max_refcount=1)
        with self.assertRaises(RefcountOverflowError):
            obj.acquire()
        obj.release()
        self.assertEqual(obj.state, "RETIRED")

    def test_invalid_max_refcount_rejected(self):
        dom = Domain()
        with self.assertRaises(ValueError):
            dom.register("x", max_refcount=0)

    def test_reader_exit_without_enter(self):
        dom = Domain()
        with self.assertRaises(LifecycleError):
            dom.reader_exit()

    def test_nested_read_sections_count_once(self):
        dom = Domain()
        with dom.read_lock():
            self.assertEqual(dom.readers_active, 1)
            with dom.read_lock():
                self.assertEqual(dom.readers_active, 1)
            self.assertEqual(dom.readers_active, 1)
        self.assertEqual(dom.readers_active, 0)

    def test_drain_timeout_while_reader_active(self):
        dom = Domain()
        obj = dom.register("x")
        entered = threading.Event()
        leave = threading.Event()

        def reader():
            with dom.read_lock():
                entered.set()
                leave.wait(10)

        t = threading.Thread(target=reader)
        t.start()
        self.assertTrue(entered.wait(5))
        obj.release()
        with self.assertRaises(DrainTimeoutError):
            dom.drain(timeout=0.2)
        leave.set()
        t.join(5)
        dom.drain(timeout=5)
        self.assertTrue(obj.is_reclaimed)

    def test_empty_drain_is_noop(self):
        dom = Domain()
        self.assertEqual(dom.drain(timeout=1), 0)

    def test_reclaim_only_after_grace_even_if_called_early(self):
        dom = Domain()
        obj = dom.register("x")
        with dom.read_lock():
            obj.release()
            self.assertEqual(dom.reclaim_ready(), 0)
            self.assertEqual(obj.state, "RETIRED")
        dom.drain(timeout=5)
        self.assertTrue(obj.is_reclaimed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
