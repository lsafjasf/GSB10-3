"""时序测试：回收完成必须发生在所有读方退出之后。

每个用例都同时做两类断言：
1. 探针断言：读方在场期间，回收标志/回收回调绝不发生；
2. 时间戳断言：t_retire < t_reader_exit <= t_reclaim。
"""

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rcuref import Domain, ReclaimDomain


class ReaderExitBeforeReclaimTest(unittest.TestCase):
    def test_reclaim_completes_only_after_reader_exits(self):
        timeline = {}
        dom = Domain()
        obj = dom.register(
            "payload",
            reclaim=lambda o: timeline.setdefault("reclaim", time.monotonic()),
        )
        reader_entered = threading.Event()
        reader_may_leave = threading.Event()

        def reader():
            with dom.read_lock():
                timeline["reader_enter"] = time.monotonic()
                reader_entered.set()
                reader_may_leave.wait(10)
                timeline["reader_exit"] = time.monotonic()

        t = threading.Thread(target=reader)
        t.start()
        self.assertTrue(reader_entered.wait(5))

        obj.release()
        timeline["retire"] = time.monotonic()
        self.assertEqual(obj.state, "RETIRED")
        self.assertEqual(dom.pending_count, 1)

        # 读方仍在临界区内：回收不得发生（探针 + 显式尝试）
        self.assertEqual(dom.reclaim_ready(), 0)
        time.sleep(0.1)
        self.assertNotIn("reclaim", timeline)
        self.assertFalse(obj.is_reclaimed)

        # 读方退出后：drain 完成回收
        reader_may_leave.set()
        dom.drain(timeout=5)
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertTrue(obj.is_reclaimed)
        self.assertEqual(dom.pending_count, 0)

        # 严格时序：退役 < 读方退出 <= 回收完成
        self.assertLess(timeline["retire"], timeline["reader_exit"])
        self.assertLessEqual(timeline["reader_exit"], timeline["reclaim"])

    def test_reclaim_waits_for_the_slowest_reader(self):
        timeline = {}
        lock = threading.Lock()
        dom = Domain()
        obj = dom.register(
            "payload",
            reclaim=lambda o: timeline.setdefault("reclaim", time.monotonic()),
        )
        both_entered = threading.Barrier(3)
        r1_leave, r2_leave = threading.Event(), threading.Event()

        def reader(name, leave_ev, hold_after_signal):
            with dom.read_lock():
                both_entered.wait(timeout=10)
                leave_ev.wait(10)
                time.sleep(hold_after_signal)
                with lock:
                    timeline[name] = time.monotonic()

        t1 = threading.Thread(target=reader, args=("r1_exit", r1_leave, 0.0))
        t2 = threading.Thread(target=reader, args=("r2_exit", r2_leave, 0.15))
        t1.start()
        t2.start()
        both_entered.wait(timeout=10)

        obj.release()
        timeline["retire"] = time.monotonic()

        # 只放走第一个读方：回收仍不得发生
        r1_leave.set()
        deadline = time.monotonic() + 2.0
        while "r1_exit" not in timeline and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertIn("r1_exit", timeline)
        self.assertEqual(dom.reclaim_ready(), 0)
        self.assertNotIn("reclaim", timeline)

        # 放走最慢的读方后回收才完成
        r2_leave.set()
        dom.drain(timeout=5)
        t1.join(5)
        t2.join(5)
        self.assertLess(timeline["r1_exit"], timeline["r2_exit"])
        self.assertLessEqual(timeline["r2_exit"], timeline["reclaim"])

    def test_no_reader_means_immediate_grace(self):
        destroyed = []
        dom = Domain()
        obj = dom.register("x", reclaim=lambda o: destroyed.append(o))
        obj.release()
        self.assertEqual(dom.reclaim_ready(), 1)
        self.assertEqual(destroyed, [obj])
        self.assertLessEqual(obj.retired_at, obj.reclaimed_at)


class BackgroundReaperTimingTest(unittest.TestCase):
    def test_background_reaper_respects_grace_period(self):
        timeline = {}
        with ReclaimDomain() as dom:
            obj = dom.register(
                "payload",
                reclaim=lambda o: timeline.setdefault(
                    "reclaim", time.monotonic()
                ),
            )
            entered = threading.Event()
            leave = threading.Event()

            def reader():
                with dom.read_lock():
                    entered.set()
                    leave.wait(10)
                    timeline["reader_exit"] = time.monotonic()

            t = threading.Thread(target=reader)
            t.start()
            self.assertTrue(entered.wait(5))
            obj.release()
            timeline["retire"] = time.monotonic()
            time.sleep(0.2)  # 后台线程有充分机会错误地提前回收
            self.assertNotIn("reclaim", timeline)
            leave.set()
            self.assertTrue(obj.wait_reclaimed(5))
            t.join(5)
        self.assertLess(timeline["retire"], timeline["reader_exit"])
        self.assertLessEqual(timeline["reader_exit"], timeline["reclaim"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
