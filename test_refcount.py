"""核心功能 + 时序 + 并发测试。运行: python3 test_refcount.py -v"""

import threading
import time
import unittest

from refcount import (
    ObjectRetiredError,
    ReclaimManager,
    RefCountOverflowError,
)


class TestBasicLifecycle(unittest.TestCase):
    def test_acquire_release_and_deferred_reclaim(self):
        destroyed = []
        mgr = ReclaimManager()
        h = mgr.create("payload", on_destroy=destroyed.append)
        self.assertEqual(h.get(), "payload")

        h2 = h._obj.acquire()          # 计数 1 -> 2
        h.release()                    # 计数 2 -> 1，不退休
        self.assertFalse(h._obj.retired)
        h2.release()                   # 计数归零 -> 进入待回收队列
        self.assertTrue(h._obj.retired)
        self.assertFalse(h._obj.destroyed)   # 不立即销毁
        self.assertEqual(mgr.pending_count, 1)

        self.assertEqual(mgr.reclaim(), 1)   # 无读者 -> 立即安全回收
        self.assertEqual(destroyed, ["payload"])
        self.assertEqual(mgr.pending_count, 0)

    def test_zero_count_only_enqueues_never_destroys_inline(self):
        destroyed = []
        mgr = ReclaimManager()
        h = mgr.create("x", on_destroy=destroyed.append)
        h.release()
        # release 返回后对象必须仍然完好：销毁只能发生在 reclaim() 里
        self.assertEqual(destroyed, [])
        self.assertEqual(mgr.pending_count, 1)


class TestReclamationTiming(unittest.TestCase):
    """读方退出与回收完成的时序：回收完成时刻必须 >= 最后读者退出时刻。"""

    def test_reclaim_waits_for_reader_exit(self):
        events = []  # (monotonic_time, label)
        mgr = ReclaimManager()
        h = mgr.create("shared", on_destroy=lambda v: events.append((time.monotonic(), "destroyed")))
        obj = h._obj

        reader_entered = threading.Event()
        may_exit = threading.Event()

        def long_reader():
            with mgr.reader():
                events.append((time.monotonic(), "reader_enter"))
                reader_entered.set()
                may_exit.wait(timeout=5)
                # 读者在临界区内仍可安全读到已退休的对象
                self.assertEqual(obj.peek(), "shared")
            events.append((time.monotonic(), "reader_exit"))

        t = threading.Thread(target=long_reader)
        t.start()
        reader_entered.wait(timeout=5)

        h.release()                    # 计数归零 -> 仅入队
        events.append((time.monotonic(), "retired"))
        self.assertEqual(mgr.reclaim(), 0)     # 有活跃读者：不能回收
        self.assertFalse(obj.destroyed)
        self.assertEqual(mgr.pending_count, 1)

        may_exit.set()                 # 读者退出临界区
        t.join(timeout=5)
        self.assertEqual(mgr.reclaim(), 1)     # 读者退出后回收成功
        self.assertTrue(obj.destroyed)

        timeline = {label: ts for ts, label in events}
        self.assertLess(timeline["reader_enter"], timeline["retired"])
        self.assertLess(timeline["retired"], timeline["reader_exit"])
        self.assertLessEqual(timeline["reader_exit"], timeline["destroyed"])
        print("\n[timing] enter=%.4f retired=%.4f exit=%.4f destroyed=%.4f (s, monotonic)"
              % (timeline["reader_enter"], timeline["retired"],
                 timeline["reader_exit"], timeline["destroyed"]))

    def test_long_lived_reader_blocks_background_reclaimer(self):
        destroyed_at = []
        mgr = ReclaimManager()
        mgr.start_background_reclaimer(interval=0.005)
        try:
            h = mgr.create("slow", on_destroy=lambda v: destroyed_at.append(time.monotonic()))
            obj = h._obj
            hold = 0.3
            entered = threading.Event()
            reader_exit_at = []

            def slow_reader():
                with mgr.reader():
                    entered.set()
                    time.sleep(hold)
                reader_exit_at.append(time.monotonic())

            t = threading.Thread(target=slow_reader)
            t.start()
            entered.wait(timeout=5)    # 确保读者已进入临界区
            h.release()                # 退休
            time.sleep(0.05)           # 给后台回收线程多次机会
            self.assertFalse(obj.destroyed, "读者未退出就被回收了")

            t.join(timeout=5)
            deadline = time.monotonic() + 2.0
            while not obj.destroyed and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertTrue(obj.destroyed, "读者退出后回收没有完成")
            self.assertGreaterEqual(destroyed_at[0], reader_exit_at[0])
            print("\n[timing] reader_exit=%.4f destroyed=%.4f (s, monotonic)"
                  % (reader_exit_at[0], destroyed_at[0]))
        finally:
            mgr.stop_background_reclaimer()

    def test_multiple_readers_reclaim_after_last_exit(self):
        destroyed = []
        mgr = ReclaimManager()
        h = mgr.create("multi", on_destroy=destroyed.append)
        exits = [threading.Event() for _ in range(3)]
        entered = [threading.Event() for _ in range(3)]

        def reader(i):
            with mgr.reader():
                entered[i].set()
                exits[i].wait(timeout=5)

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for e in entered:
            e.wait(timeout=5)

        h.release()
        for i in range(3):
            exits[i].set()                     # 放行第 i 个读者
            time.sleep(0.01)
            if i < 2:
                self.assertEqual(mgr.reclaim(), 0, f"还有 {2 - i} 个读者，不应回收")
            else:
                self.assertEqual(mgr.reclaim(), 1, "最后一个读者退出后应可回收")
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(destroyed, ["multi"])


class TestConcurrency(unittest.TestCase):
    def test_concurrent_acquire_release_single_object(self):
        mgr = ReclaimManager()
        h0 = mgr.create("hot")
        obj = h0._obj
        workers, loops = 8, 2000
        errors = []
        barrier = threading.Barrier(workers)

        def worker():
            try:
                barrier.wait(timeout=5)
                for _ in range(loops):
                    h = obj.acquire()
                    self.assertEqual(h.get(), "hot")
                    h.release()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(errors, [])
        self.assertEqual(obj.refcount, 1, "计数必须精确回到初始值")
        h0.release()
        self.assertTrue(obj.retired)
        self.assertEqual(mgr.reclaim(), 1)

    def test_concurrent_mixed_readers_and_reclaim(self):
        mgr = ReclaimManager()
        stop = threading.Event()
        errors = []
        destroyed = []
        lock = threading.Lock()

        def churn():
            try:
                while not stop.is_set():
                    h = mgr.create("item", on_destroy=lambda v: destroyed.append(v))
                    h2 = h._obj.acquire()
                    h.release()
                    h2.release()
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

        def read_loop():
            try:
                while not stop.is_set():
                    with mgr.reader():
                        time.sleep(0.0005)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

        threads = ([threading.Thread(target=churn) for _ in range(4)]
                   + [threading.Thread(target=read_loop) for _ in range(2)])
        for t in threads:
            t.start()
        time.sleep(1.0)
        stop.set()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [])
        # 所有读者退出后，剩余待回收对象必须能全部清掉
        mgr.reclaim()
        self.assertEqual(mgr.pending_count, 0)
        self.assertGreater(len(destroyed), 0)

    def test_overflow_under_concurrency(self):
        mgr = ReclaimManager(max_refcount=64)
        h0 = mgr.create("capped")
        obj = h0._obj
        successes = []
        overflows = []
        lock = threading.Lock()
        barrier = threading.Barrier(16)

        def worker():
            barrier.wait(timeout=5)
            for _ in range(16):
                try:
                    h = obj.acquire()
                except RefCountOverflowError:
                    with lock:
                        overflows.append(1)
                else:
                    with lock:
                        successes.append(h)

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len(successes), 63, "成功获取数必须恰好等于剩余额度")
        self.assertGreater(len(overflows), 0)
        self.assertEqual(obj.refcount, 64)
        for h in successes:
            h.release()
        h0.release()
        self.assertEqual(mgr.reclaim(), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
