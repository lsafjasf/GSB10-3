"""ReentrantRWLock 自测: 功能、深度断言、边界、跨线程争抢、压力。

运行: python3 -m unittest -v tests.test_rwlock
"""

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reentrant_rwlock import (  # noqa: E402
    LockReleaseError,
    LockTimeoutError,
    LockUpgradeError,
    ReentrantRWLock,
)


def join_or_fail(thread, timeout=3.0):
    """若线程在超时内未结束, 判定为死锁并失败。"""
    thread.join(timeout)
    if thread.is_alive():
        raise AssertionError("线程疑似死锁, %.1fs 内未结束" % timeout)


class SingleThreadDepthTests(unittest.TestCase):
    def test_single_acquire_and_depth_zero(self):
        lock = ReentrantRWLock()
        lock.acquire_read()
        self.assertEqual(lock.read_depth(), 1)
        lock.release_read()
        self.assertEqual((lock.read_depth(), lock.write_depth(),
                          lock.subsumed_read_depth()), (0, 0, 0))

        lock.acquire_write()
        self.assertEqual(lock.write_depth(), 1)
        lock.release_write()
        self.assertEqual((lock.read_depth(), lock.write_depth()), (0, 0))

    def test_read_reenter_depth(self):
        lock = ReentrantRWLock()
        for expected in (1, 2, 3):
            lock.acquire_read()
            self.assertEqual(lock.read_depth(), expected)
        self.assertEqual(lock.write_depth(), 0)
        for expected in (2, 1, 0):
            lock.release_read()
            self.assertEqual(lock.read_depth(), expected)

    def test_write_reenter_depth_and_real_release(self):
        lock = ReentrantRWLock()
        for expected in (1, 2, 3):
            lock.acquire_write()
            self.assertEqual(lock.write_depth(), expected)
        snap = lock.snapshot()
        self.assertEqual(snap["write"], 3)
        self.assertEqual(snap["global_write_depth"], 3)
        self.assertEqual(snap["is_writer"], 1)
        # 深度未归零时, 其他写者必须拿不到。
        gate = threading.Event()
        errors = []

        def competing_writer():
            try:
                lock.acquire_write(timeout=2.0)
                gate.set()
                lock.release_write()
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        other = threading.Thread(target=competing_writer)
        other.start()
        time.sleep(0.2)
        self.assertFalse(gate.is_set(), "写重入期间锁不应被交出")

        for expected in (2, 1, 0):
            lock.release_write()
            self.assertEqual(lock.write_depth(), expected)
        join_or_fail(other)
        self.assertTrue(gate.is_set())
        self.assertEqual(errors, [])

    def test_write_then_read_is_subsumed_not_downgraded(self):
        lock = ReentrantRWLock()
        lock.acquire_write()
        lock.acquire_read()  # 规则 R3: 并入, 不是降级
        snap = lock.snapshot()
        self.assertEqual(snap["write"], 1)
        self.assertEqual(snap["subsumed_read"], 1)
        self.assertEqual(snap["read"], 0, "并入读帧绝不能计为真实读锁")
        self.assertEqual(snap["global_reader_count"], 0,
                         "写者仍是互斥持有者, 全局读者数必须为 0")
        self.assertEqual(snap["is_writer"], 1)

        # 写期间再次重入写锁, 并入读帧夹在中间。
        lock.acquire_write()
        self.assertEqual(lock.write_depth(), 2)
        # 栈顶是写帧: 此时不能用 release_read 越过写帧释放并入读帧。
        with self.assertRaises(LockReleaseError):
            lock.release_read()
        lock.release_write()  # 回到 [WRITE, SUBSUMED_READ]
        lock.release_read()
        self.assertEqual(lock.subsumed_read_depth(), 0)
        self.assertEqual(lock.write_depth(), 1, "释放并入读帧不影响写深度")
        lock.release_write()
        self.assertEqual(lock.snapshot()["global_write_depth"], 0)


class ErrorBoundaryTests(unittest.TestCase):
    def test_release_without_hold(self):
        lock = ReentrantRWLock()
        with self.assertRaises(LockReleaseError):
            lock.release_read()
        with self.assertRaises(LockReleaseError):
            lock.release_write()

    def test_release_order_mismatch(self):
        lock = ReentrantRWLock()
        lock.acquire_read()
        with self.assertRaises(LockReleaseError):
            lock.release_write()
        lock.release_read()

        lock.acquire_write()
        with self.assertRaises(LockReleaseError):
            lock.release_read()  # 栈顶是 WRITE
        lock.release_write()

    def test_write_acquire_while_reader_is_explicit_error(self):
        lock = ReentrantRWLock()
        lock.acquire_read()
        start = time.monotonic()
        with self.assertRaises(LockUpgradeError):
            lock.acquire_write()  # 不允许隐式升级, 且必须立即返回
        self.assertLess(time.monotonic() - start, 0.2)
        # 原读锁不受影响。
        self.assertEqual(lock.read_depth(), 1)
        lock.release_read()

    def test_upgrade_combinations(self):
        lock = ReentrantRWLock()
        with self.assertRaises(LockUpgradeError):
            lock.upgrade()  # 未持有读锁

        lock.acquire_write()
        with self.assertRaises(LockUpgradeError):
            lock.upgrade()  # 已是写者
        lock.release_write()

        lock.acquire_read()
        lock.acquire_read()
        with self.assertRaises(LockUpgradeError):
            lock.upgrade()  # 读重入状态不允许升级
        lock.release_read()

        entered = threading.Event()
        release_other = threading.Event()

        def other_reader():
            lock.acquire_read()
            entered.set()
            release_other.wait(2.0)
            lock.release_read()

        t = threading.Thread(target=other_reader)
        t.start()
        self.assertTrue(entered.wait(2.0))
        # 存在其他读者: 阻塞升级会自死锁, 必须立即报错而非等待。
        start = time.monotonic()
        with self.assertRaises(LockUpgradeError):
            lock.upgrade()
        self.assertLess(time.monotonic() - start, 0.2)
        release_other.set()
        join_or_fail(t)
        lock.release_read()

    def test_upgrade_blocked_by_waiting_writer(self):
        lock = ReentrantRWLock()
        lock.acquire_read()
        writer_ready = threading.Event()

        def waiting_writer():
            writer_ready.set()
            lock.acquire_write()  # 唯一读者是主线程, 这里阻塞
            lock.release_write()

        t = threading.Thread(target=waiting_writer)
        t.start()
        self.assertTrue(writer_ready.wait(2.0))
        time.sleep(0.1)  # 确保等待写者已登记
        with self.assertRaises(LockUpgradeError):
            lock.upgrade()
        lock.release_read()  # 放行等待写者
        join_or_fail(t)

    def test_downgrade_rules(self):
        lock = ReentrantRWLock()
        with self.assertRaises(LockUpgradeError):
            lock.downgrade()  # 未持写锁

        lock.acquire_write()
        lock.acquire_write()
        with self.assertRaises(LockUpgradeError):
            lock.downgrade()  # 仍有重入写帧
        lock.release_write()

        reader_entered = threading.Event()

        def reader():
            lock.acquire_read()
            reader_entered.set()
            lock.release_read()

        t = threading.Thread(target=reader)
        t.start()
        self.assertFalse(reader_entered.wait(0.2))
        lock.downgrade()  # 显式降级: 写帧变读帧, 写互斥立刻交出
        self.assertEqual((lock.read_depth(), lock.write_depth()), (1, 0))
        join_or_fail(t)
        self.assertTrue(reader_entered.is_set())
        lock.release_read()

    def test_timeout_leaves_lock_usable(self):
        lock = ReentrantRWLock()
        lock.acquire_write()
        outcomes = []

        def contender():
            for acquire in (lock.acquire_write, lock.acquire_read):
                try:
                    acquire(timeout=0.1)
                except LockTimeoutError:
                    outcomes.append("timeout")
                else:
                    outcomes.append("acquired")

        t = threading.Thread(target=contender)
        t.start()
        join_or_fail(t)
        self.assertEqual(outcomes, ["timeout", "timeout"])
        lock.release_write()
        lock.acquire_read(timeout=0.5)  # 超时后锁仍可正常获取
        lock.release_read()

    def test_context_managers(self):
        lock = ReentrantRWLock()
        with lock.read_lock():
            self.assertEqual(lock.read_depth(), 1)
            with self.assertRaises(LockUpgradeError):
                with lock.write_lock():
                    self.fail("读持有期间禁止隐式取写锁")
        self.assertEqual(lock.read_depth(), 0)

    def test_explicit_owner_ids_are_distinct(self):
        lock = ReentrantRWLock()
        lock.acquire_read(owner="a")
        lock.acquire_read(owner="b")  # 不同 owner, 多读共存
        self.assertEqual(lock.read_depth(owner="a"), 1)
        with self.assertRaises(LockTimeoutError):
            lock.acquire_write(owner="c", timeout=0.1)
        lock.release_read(owner="a")
        lock.release_read(owner="b")
        lock.acquire_write(owner="c", timeout=0.5)
        lock.release_write(owner="c")


class CrossThreadContentionTests(unittest.TestCase):
    def test_concurrent_readers_coexist(self):
        lock = ReentrantRWLock()
        barrier = threading.Barrier(3)
        inside = [0]
        max_inside = [0]
        guard = threading.Lock()

        def reader():
            barrier.wait(timeout=3.0)
            lock.acquire_read()
            with guard:
                inside[0] += 1
                max_inside[0] = max(max_inside[0], inside[0])
            time.sleep(0.05)
            with guard:
                inside[0] -= 1
            lock.release_read()

        threads = [threading.Thread(target=reader) for _ in range(2)]
        for t in threads:
            t.start()
        barrier.wait(timeout=3.0)
        for t in threads:
            join_or_fail(t)
        self.assertEqual(max_inside[0], 2, "两个读者应能同时持有读锁")

    def test_writer_excludes_readers_and_writers(self):
        lock = ReentrantRWLock()
        lock.acquire_write()
        reader_done = threading.Event()
        writer_done = threading.Event()

        def reader():
            lock.acquire_read()
            reader_done.set()
            lock.release_read()

        def writer():
            lock.acquire_write()
            writer_done.set()
            lock.release_write()

        t1 = threading.Thread(target=reader)
        t2 = threading.Thread(target=writer)
        t1.start()
        t2.start()
        time.sleep(0.2)
        self.assertFalse(reader_done.is_set(), "写者持有时读者必须等待")
        self.assertFalse(writer_done.is_set(), "写者持有时写者必须等待")
        lock.release_write()
        join_or_fail(t1)
        join_or_fail(t2)
        self.assertTrue(reader_done.is_set() and writer_done.is_set())

    def test_writer_preference_blocks_new_readers(self):
        lock = ReentrantRWLock()
        lock.acquire_read()
        writer_waiting = threading.Event()
        writer_done = threading.Event()
        late_reader_done = threading.Event()

        def writer():
            writer_waiting.set()
            lock.acquire_write()
            writer_done.set()
            lock.release_write()

        def late_reader():
            writer_waiting.wait(2.0)
            time.sleep(0.1)  # 确保写者已登记等待
            lock.acquire_read()
            late_reader_done.set()
            lock.release_read()

        tw = threading.Thread(target=writer)
        tr = threading.Thread(target=late_reader)
        tw.start()
        tr.start()
        time.sleep(0.3)
        self.assertFalse(writer_done.is_set())
        self.assertFalse(late_reader_done.is_set(),
                         "有等待写者时, 新读者必须让位(写者优先)")
        lock.release_read()
        join_or_fail(tw)
        join_or_fail(tr)
        self.assertTrue(writer_done.is_set() and late_reader_done.is_set())

    def test_counter_integrity_under_contention(self):
        lock = ReentrantRWLock()
        counter = [0]
        stop = threading.Event()
        errors = []

        def writer():
            while not stop.is_set():
                lock.acquire_write(timeout=2.0)
                lock.acquire_write()  # 重入一层
                value = counter[0]
                counter[0] = value + 1
                self_check = counter[0]
                lock.release_write()
                if self_check != value + 1:
                    errors.append("写临界区被穿透")
                lock.release_write()

        def reader():
            while not stop.is_set():
                lock.acquire_read(timeout=2.0)
                lock.acquire_read()  # 重入一层
                snapshot = counter[0]
                if snapshot != counter[0]:
                    errors.append("读临界区观察到并发写")
                lock.release_read()
                lock.release_read()

        threads = ([threading.Thread(target=writer) for _ in range(3)]
                   + [threading.Thread(target=reader) for _ in range(3)])
        for t in threads:
            t.start()
        time.sleep(1.0)
        stop.set()
        for t in threads:
            join_or_fail(t, timeout=5.0)
        self.assertEqual(errors, [])
        self.assertGreater(counter[0], 0)

    def test_reentrant_read_survives_waiting_writer(self):
        # 持有者重入读锁时, 即使已有写者排队也不得阻塞(否则自死锁)。
        lock = ReentrantRWLock()
        lock.acquire_read()
        writer_waiting = threading.Event()

        def writer():
            writer_waiting.set()
            lock.acquire_write()
            lock.release_write()

        t = threading.Thread(target=writer)
        t.start()
        self.assertTrue(writer_waiting.wait(2.0))
        time.sleep(0.1)
        start = time.monotonic()
        lock.acquire_read()  # 重入: 必须立即成功
        self.assertLess(time.monotonic() - start, 0.2)
        self.assertEqual(lock.read_depth(), 2)
        lock.release_read()
        lock.release_read()
        join_or_fail(t)


class StressTests(unittest.TestCase):
    def test_mixed_operations_stress(self):
        lock = ReentrantRWLock()
        shared = {"value": 0}
        stop = threading.Event()
        errors = []

        def worker(worker_id):
            try:
                while not stop.is_set():
                    action = worker_id % 4
                    if action == 0:
                        lock.acquire_write(timeout=2.0)
                        lock.acquire_write()
                        shared["value"] += 1
                        lock.release_write()
                        lock.release_write()
                    elif action == 1:
                        lock.acquire_read(timeout=2.0)
                        lock.acquire_read()
                        _ = shared["value"]
                        lock.release_read()
                        lock.release_read()
                    elif action == 2:
                        lock.acquire_write(timeout=2.0)
                        lock.acquire_read()  # SUBSUMED_READ
                        shared["value"] += 1
                        lock.release_read()
                        lock.downgrade()
                        _ = shared["value"]
                        lock.release_read()
                    else:
                        lock.acquire_read(timeout=2.0)
                        try:
                            lock.upgrade()
                        except LockUpgradeError:
                            lock.release_read()
                        else:
                            shared["value"] += 1
                            lock.release_write()
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(8)]
        for t in threads:
            t.start()
        time.sleep(1.5)
        stop.set()
        for t in threads:
            join_or_fail(t, timeout=5.0)
        self.assertEqual(errors, [])
        self.assertGreater(shared["value"], 0)
        # 所有线程退出后, 锁必须完全干净。
        self.assertEqual(lock.snapshot()["global_write_depth"], 0)
        self.assertEqual(lock.snapshot()["global_reader_count"], 0)


if __name__ == "__main__":
    unittest.main()
