"""ReentrantReadWriteLock 自测：并发断言 + 重入深度 + 边界用例。

运行：python3 -m unittest -v test_reentrant_rw_lock
"""

import threading
import time
import unittest

from reentrant_rw_lock import (
    AcquireTimeout,
    DowngradeError,
    ReentrantReadWriteLock,
    ReleaseUnheldError,
    UpgradeError,
)


class SingleThreadTests(unittest.TestCase):
    """单线程：单次获取、重入深度、读写交替、升降级规则。"""

    def setUp(self):
        self.lock = ReentrantReadWriteLock()

    def test_single_read_acquire_release(self):
        self.assertTrue(self.lock.acquire_read())
        self.assertEqual(self.lock.read_depth(), 1)
        self.assertEqual(self.lock.reader_count(), 1)
        self.lock.release_read()
        self.assertEqual(self.lock.read_depth(), 0)
        self.assertEqual(self.lock.reader_count(), 0)

    def test_single_write_acquire_release(self):
        self.assertTrue(self.lock.acquire_write())
        self.assertEqual(self.lock.write_depth(), 1)
        self.assertTrue(self.lock.is_write_locked)
        self.lock.release_write()
        self.assertEqual(self.lock.write_depth(), 0)
        self.assertFalse(self.lock.is_write_locked)

    def test_read_reentrancy_depth(self):
        for expected in (1, 2, 3):
            self.lock.acquire_read()
            self.assertEqual(self.lock.read_depth(), expected)
        # 深度未归零前，锁仍被持有（reader_count 不变）。
        self.lock.release_read()
        self.assertEqual(self.lock.read_depth(), 2)
        self.assertEqual(self.lock.reader_count(), 1)
        self.lock.release_read()
        self.lock.release_read()
        self.assertEqual(self.lock.read_depth(), 0)
        self.assertEqual(self.lock.reader_count(), 0)

    def test_write_reentrancy_depth(self):
        for expected in (1, 2, 3):
            self.lock.acquire_write()
            self.assertEqual(self.lock.write_depth(), expected)
        self.lock.release_write()
        self.assertEqual(self.lock.write_depth(), 2)
        self.assertTrue(self.lock.is_write_locked)
        self.lock.release_write()
        self.lock.release_write()
        self.assertEqual(self.lock.write_depth(), 0)
        self.assertFalse(self.lock.is_write_locked)

    def test_read_write_alternation(self):
        for _ in range(5):
            with self.lock.read_locked():
                self.assertEqual(self.lock.read_depth(), 1)
            with self.lock.write_locked():
                self.assertEqual(self.lock.write_depth(), 1)
        self.assertEqual(self.lock.reader_count(), 0)
        self.assertFalse(self.lock.is_write_locked)

    def test_writer_acquires_read_is_not_a_downgrade(self):
        """写锁持有期间拿读锁：允许、独立记账，写锁权限原样保留。"""
        self.lock.acquire_write()
        self.lock.acquire_write()  # 写深度 2
        self.lock.acquire_read()
        self.lock.acquire_read()  # 读深度 2
        self.assertEqual(self.lock.write_depth(), 2)
        self.assertEqual(self.lock.read_depth(), 2)
        # 释放两层读锁后，写锁仍在：证明没有被悄悄降级。
        self.lock.release_read()
        self.lock.release_read()
        self.assertTrue(self.lock.is_write_locked)
        self.assertEqual(self.lock.write_depth(), 2)
        self.lock.release_write()
        self.lock.release_write()
        self.assertFalse(self.lock.is_write_locked)

    def test_sole_reader_upgrade_allowed(self):
        self.lock.acquire_read()
        self.assertTrue(self.lock.acquire_write())  # 唯一读者就地升级
        self.assertEqual(self.lock.write_depth(), 1)
        self.assertEqual(self.lock.read_depth(), 0)
        self.assertEqual(self.lock.reader_count(), 0)
        self.lock.release_write()

    def test_upgrade_rejected_with_other_readers(self):
        other = threading.Event()
        release = threading.Event()

        def reader():
            self.lock.acquire_read()
            other.set()
            release.wait(5)
            self.lock.release_read()

        t = threading.Thread(target=reader)
        t.start()
        other.wait(5)
        self.lock.acquire_read()
        with self.assertRaises(UpgradeError):
            self.lock.acquire_write()  # 必须报错而不是死锁
        self.lock.release_read()
        release.set()
        t.join(5)

    def test_downgrade_success(self):
        self.lock.acquire_write()
        self.lock.downgrade()
        self.assertFalse(self.lock.is_write_locked)
        self.assertEqual(self.lock.write_depth(), 0)
        self.assertEqual(self.lock.read_depth(), 1)
        self.lock.release_read()

    def test_downgrade_requires_write_depth_one(self):
        self.lock.acquire_write()
        self.lock.acquire_write()  # 写深度 2
        with self.assertRaises(DowngradeError):
            self.lock.downgrade()
        self.lock.release_write()
        self.lock.downgrade()  # 深度回到 1 后允许
        self.lock.release_read()

    def test_downgrade_without_write_lock_raises(self):
        with self.assertRaises(DowngradeError):
            self.lock.downgrade()

    def test_release_unheld_raises(self):
        with self.assertRaises(ReleaseUnheldError):
            self.lock.release_read()
        with self.assertRaises(ReleaseUnheldError):
            self.lock.release_write()

    def test_release_from_other_thread_raises(self):
        self.lock.acquire_write()
        errors = []

        def stranger():
            try:
                self.lock.release_write()
            except ReleaseUnheldError:
                errors.append("write")
            try:
                self.lock.release_read()
            except ReleaseUnheldError:
                errors.append("read")

        t = threading.Thread(target=stranger)
        t.start()
        t.join(5)
        self.assertEqual(sorted(errors), ["read", "write"])
        self.lock.release_write()

    def test_nonblocking_acquire_timeout(self):
        held = threading.Event()
        t = threading.Thread(target=lambda: (
            self.lock.acquire_write(), held.set(), time.sleep(1),
            self.lock.release_write()))
        t.start()
        held.wait(5)
        with self.assertRaises(AcquireTimeout):
            self.lock.acquire_read(timeout=0)
        t.join(5)

    def test_timed_acquire_returns_false(self):
        held = threading.Event()
        t = threading.Thread(target=lambda: (
            self.lock.acquire_write(), held.set(), time.sleep(1),
            self.lock.release_write()))
        t.start()
        held.wait(5)
        start = time.monotonic()
        self.assertFalse(self.lock.acquire_read(timeout=0.1))
        self.assertGreaterEqual(time.monotonic() - start, 0.09)
        t.join(5)


class CrossThreadTests(unittest.TestCase):
    """跨线程争抢：互斥性、写者优先、并发一致性。"""

    def setUp(self):
        self.lock = ReentrantReadWriteLock()

    def test_writer_blocks_reader_and_writer(self):
        self.lock.acquire_write()
        entered = []

        def reader():
            with self.lock.read_locked():
                entered.append("reader")

        def writer():
            with self.lock.write_locked():
                entered.append("writer")

        t1 = threading.Thread(target=reader)
        t2 = threading.Thread(target=writer)
        t1.start()
        t2.start()
        time.sleep(0.1)
        self.assertEqual(entered, [])  # 双方都被阻塞
        self.lock.release_write()
        t1.join(5)
        t2.join(5)
        self.assertEqual(sorted(entered), ["reader", "writer"])

    def test_reader_blocks_writer(self):
        self.lock.acquire_read()
        done = threading.Event()

        def writer():
            with self.lock.write_locked():
                done.set()

        t = threading.Thread(target=writer)
        t.start()
        time.sleep(0.1)
        self.assertFalse(done.is_set())
        self.lock.release_read()
        t.join(5)
        self.assertTrue(done.is_set())

    def test_writer_preference_over_new_readers(self):
        """有等待写者时新读者排队；已持锁读者的重入不受影响。"""
        self.lock.acquire_read()
        order = []
        writer_waiting = threading.Event()

        def writer():
            writer_waiting.set()
            with self.lock.write_locked():
                order.append("writer")

        def new_reader():
            writer_waiting.wait(5)
            while self.lock.writers_waiting() < 1:  # 等写者确实排队
                time.sleep(0.005)
            with self.lock.read_locked():
                order.append("new_reader")

        tw = threading.Thread(target=writer)
        tr = threading.Thread(target=new_reader)
        tw.start()
        tr.start()
        time.sleep(0.05)
        # 本线程（已持读锁）重入读锁必须立即成功，不被等待写者卡住。
        self.assertTrue(self.lock.acquire_read(timeout=0))
        self.lock.release_read()
        self.lock.release_read()  # 释放外层读锁，放行写者
        tw.join(5)
        tr.join(5)
        self.assertEqual(order, ["writer", "new_reader"])

    def test_concurrent_counter_consistency(self):
        """多写者重入递增 + 多读者校验，断言读写互斥与计数正确。"""
        counter = {"value": 0}
        violations = []
        writers, readers, rounds = 4, 4, 200

        def writer():
            for _ in range(rounds):
                with self.lock.write_locked():
                    with self.lock.write_locked():  # 重入一层
                        before = counter["value"]
                        counter["value"] = before + 1
                        if counter["value"] != before + 1:
                            violations.append("torn write")

        snapshot_ok = []

        def reader():
            for _ in range(rounds):
                with self.lock.read_locked():
                    with self.lock.read_locked():  # 重入一层
                        v1 = counter["value"]
                        v2 = counter["value"]
                        snapshot_ok.append(v1 == v2)

        threads = [
            threading.Thread(target=writer if i % 2 == 0 else reader)
            for i in range(writers + readers)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)

        self.assertEqual(violations, [])
        self.assertTrue(all(snapshot_ok))
        self.assertEqual(counter["value"], writers * rounds)
        # 全部结束后锁必须完全归零。
        self.assertEqual(self.lock.reader_count(), 0)
        self.assertFalse(self.lock.is_write_locked)

    def test_writer_sees_own_writes_through_read_lock(self):
        """写者持写锁期间通过读锁读到自己的修改（写->读 规则的用途）。"""
        data = {"x": 0}
        with self.lock.write_locked():
            data["x"] = 42
            with self.lock.read_locked():
                self.assertEqual(data["x"], 42)
            # 读锁释放后写锁仍在，数据仍受独占保护。
            self.assertTrue(self.lock.is_write_locked)

    def test_write_released_while_read_held_becomes_shared_reader(self):
        """边界：写锁全释放但读账仍在 -> 自动变为普通读锁（可共享，拒写者）。"""
        joined = threading.Event()
        release_other = threading.Event()

        def other_reader():
            with self.lock.read_locked():
                joined.set()
                release_other.wait(5)

        self.lock.acquire_write()
        self.lock.acquire_read()  # 写者拿读锁：独立记账
        t = threading.Thread(target=other_reader)
        t.start()
        time.sleep(0.1)
        self.assertFalse(joined.is_set())  # 写锁还在，读者进不来
        self.lock.release_write()          # 写深度归零，读账仍在
        self.assertTrue(joined.wait(5))    # 其他读者现在可以共享进入
        self.assertEqual(self.lock.reader_count(), 2)

        outcomes = []

        def stranger_writer():
            try:
                self.lock.acquire_write(timeout=0)
            except AcquireTimeout:
                outcomes.append("timeout")

        s = threading.Thread(target=stranger_writer)
        s.start()
        s.join(5)
        self.assertEqual(outcomes, ["timeout"])  # 有读者在场，写者拿不到
        release_other.set()
        t.join(5)
        self.lock.release_read()
        self.assertEqual(self.lock.reader_count(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
