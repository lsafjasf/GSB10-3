"""BoundedBatchQueue 自测（仅标准库 unittest）。

运行：python3 -m unittest -v  或  python3 test_bounded_batch_queue.py
"""

import threading
import time
import unittest
from queue import Empty

from bounded_batch_queue import (
    QUEUE_CLOSED,
    BoundedBatchQueue,
    QueueClosed,
)


def spin_wait(predicate, timeout=2.0):
    """测试辅助：轮询等待断言条件成立（仅用于测试同步，不属于被测代码）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


class TestTrimRule(unittest.TestCase):
    """裁剪规则：条数 = min(批量上限, 剩余容量, 已入队数量)。"""

    def test_trim_by_enqueued_count(self):
        # 已入队 3 < 上限 10、容量 10 -> 取 3
        q = BoundedBatchQueue(16)
        for i in range(3):
            q.put(i)
        self.assertEqual(q.pull_batch(max_items=10, free_space=10), [0, 1, 2])

    def test_trim_by_max_items(self):
        # 上限 4 < 已入队 7、容量 10 -> 取 4，剩 3
        q = BoundedBatchQueue(16)
        for i in range(7):
            q.put(i)
        self.assertEqual(q.pull_batch(max_items=4, free_space=10), [0, 1, 2, 3])
        self.assertEqual(q.size(), 3)

    def test_trim_by_free_space(self):
        # 剩余容量 2 < 上限 10、已入队 5 -> 取 2，剩 3
        q = BoundedBatchQueue(16)
        for i in range(5):
            q.put(i)
        self.assertEqual(q.pull_batch(max_items=10, free_space=2), [0, 1])
        self.assertEqual(q.size(), 3)

    def test_trim_all_equal(self):
        # 三者相等 -> 全部取走
        q = BoundedBatchQueue(16)
        for i in range(4):
            q.put(i)
        self.assertEqual(q.pull_batch(max_items=4, free_space=4), [0, 1, 2, 3])
        self.assertEqual(q.size(), 0)

    def test_invalid_limits_rejected(self):
        q = BoundedBatchQueue(4)
        with self.assertRaises(ValueError):
            q.pull_batch(max_items=0, free_space=1)
        with self.assertRaises(ValueError):
            q.pull_batch(max_items=1, free_space=0)


class TestEdgeCases(unittest.TestCase):
    """单元素、满批、空队列等待、关闭时仍有元素。"""

    def test_single_element(self):
        q = BoundedBatchQueue(8)
        q.put("only")
        self.assertEqual(q.pull_batch(max_items=16, free_space=16), ["only"])
        self.assertEqual(q.size(), 0)

    def test_full_batch_pull(self):
        # 队列塞满到 capacity，一次性按满批取走
        q = BoundedBatchQueue(5)
        for i in range(5):
            q.put(i)
        self.assertEqual(q.size(), 5)
        self.assertEqual(
            q.pull_batch(max_items=5, free_space=5), [0, 1, 2, 3, 4]
        )
        self.assertEqual(q.size(), 0)

    def test_empty_queue_waits_for_producer(self):
        # 空队列：消费者阻塞等待，生产者稍后入队，消费者拿到数据
        q = BoundedBatchQueue(4)
        got = []

        def consumer():
            got.extend(q.pull_batch(max_items=2, free_space=2))

        t = threading.Thread(target=consumer)
        t.start()
        # 等消费者确实进入阻塞等待
        self.assertTrue(spin_wait(lambda: q._wait_calls >= 1))
        time.sleep(0.05)  # 给消费者充分“空转”的机会
        self.assertEqual(got, [])  # 尚未返回
        q.put(1)
        q.put(2)
        t.join(timeout=2)
        self.assertFalse(t.is_alive())
        self.assertEqual(got, [1, 2])

    def test_close_with_pending_elements(self):
        # 关闭时队列里仍有元素：存量可取完，之后才收到结束信号
        q = BoundedBatchQueue(8)
        for i in range(3):
            q.put(i)
        q.close()
        self.assertEqual(q.pull_batch(max_items=2, free_space=2), [0, 1])
        self.assertEqual(q.pull_batch(max_items=8, free_space=8), [2])
        self.assertIs(q.pull_batch(max_items=8, free_space=8), QUEUE_CLOSED)

    def test_close_wakes_blocked_waiters_with_end_signal(self):
        # 关闭瞬间仍有等待者：等待者被唤醒并收到结束信号
        q = BoundedBatchQueue(4)
        results = []

        def waiter():
            results.append(q.pull_batch(max_items=4, free_space=4))

        threads = [threading.Thread(target=waiter) for _ in range(3)]
        for t in threads:
            t.start()
        self.assertTrue(spin_wait(lambda: q._wait_calls >= 3))
        q.close()
        for t in threads:
            t.join(timeout=2)
        self.assertEqual(len(results), 3)
        for r in results:
            self.assertIs(r, QUEUE_CLOSED)

    def test_close_then_drain_then_end_signal(self):
        # 等待者阻塞期间 close()，但队列里还有元素：
        # 等待者先拿到存量元素，下一次拉取才收到结束信号
        q = BoundedBatchQueue(4)
        q.put("a")
        q.put("b")
        got = []

        def waiter():
            got.append(q.pull_batch(max_items=4, free_space=4))
            got.append(q.pull_batch(max_items=4, free_space=4))

        t = threading.Thread(target=waiter)
        t.start()
        # 元素本就在队列里，第一次 pull 立即返回并阻塞在第二次上
        self.assertTrue(spin_wait(lambda: q._wait_calls >= 1))
        q.close()
        t.join(timeout=2)
        self.assertFalse(t.is_alive())
        self.assertEqual(got[0], ["a", "b"])
        self.assertIs(got[1], QUEUE_CLOSED)

    def test_put_after_close_raises(self):
        q = BoundedBatchQueue(2)
        q.close()
        with self.assertRaises(QueueClosed):
            q.put(1)

    def test_pull_timeout_raises_empty(self):
        q = BoundedBatchQueue(2)
        with self.assertRaises(Empty):
            q.pull_batch(max_items=1, free_space=1, timeout=0.05)


class TestNoBusyWait(unittest.TestCase):
    """空转断言：空队列等待期间不允许非阻塞重检循环。"""

    def test_zero_busy_spins_while_waiting(self):
        q = BoundedBatchQueue(4)
        done = threading.Event()

        def consumer():
            q.pull_batch(max_items=1, free_space=1)
            done.set()

        t = threading.Thread(target=consumer)
        t.start()
        self.assertTrue(spin_wait(lambda: q._wait_calls >= 1))
        # 阻塞期间多次采样：空转次数必须恒为 0
        for _ in range(20):
            self.assertEqual(q.busy_spins, 0)
            time.sleep(0.01)
        q.put("x")
        self.assertTrue(done.wait(timeout=2))
        t.join(timeout=2)
        # 返回之后空转次数仍为 0
        self.assertEqual(q.busy_spins, 0)

    def test_zero_busy_spins_under_repeated_empty_waits(self):
        # 反复经历 空->有->空 的循环，空转计数始终保持 0
        q = BoundedBatchQueue(2)
        for round_no in range(10):
            got = []

            def consumer():
                got.extend(q.pull_batch(max_items=1, free_space=1))

            t = threading.Thread(target=consumer)
            t.start()
            self.assertTrue(spin_wait(lambda: q._wait_calls >= round_no + 1))
            self.assertEqual(q.busy_spins, 0)
            q.put(round_no)
            t.join(timeout=2)
            self.assertEqual(got, [round_no])
            self.assertEqual(q.busy_spins, 0)

    def test_zero_busy_spins_on_close_wakeup(self):
        # close() 唤醒等待者也不产生空转
        q = BoundedBatchQueue(4)
        t = threading.Thread(
            target=lambda: q.pull_batch(max_items=1, free_space=1)
        )
        t.start()
        self.assertTrue(spin_wait(lambda: q._wait_calls >= 1))
        q.close()
        t.join(timeout=2)
        self.assertEqual(q.busy_spins, 0)


class TestConcurrency(unittest.TestCase):
    def test_producer_consumer_stream(self):
        # 多批流式拉取：总量守恒、FIFO 顺序保持
        q = BoundedBatchQueue(8)
        total = 100
        consumed = []

        def producer():
            for i in range(total):
                q.put(i)
            q.close()

        def consumer():
            while True:
                batch = q.pull_batch(max_items=7, free_space=7)
                if batch is QUEUE_CLOSED:
                    break
                consumed.extend(batch)

        pt = threading.Thread(target=producer)
        ct = threading.Thread(target=consumer)
        pt.start()
        ct.start()
        pt.join(timeout=10)
        ct.join(timeout=10)
        self.assertEqual(consumed, list(range(total)))
        self.assertEqual(q.busy_spins, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
