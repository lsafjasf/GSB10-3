"""BoundedBatchQueue 自测：空转断言、裁剪规则、边界与关闭语义。"""

import threading
import time
import unittest

from batch_queue import BoundedBatchQueue, QueueClosed


def pull_in_thread(queue, limit, remaining=None):
    """在子线程中执行 pull_batch，返回 (result_holder, thread)。"""
    holder = {}

    def run():
        holder["batch"] = queue.pull_batch(limit, remaining)

    t = threading.Thread(target=run)
    t.start()
    return holder, t


class TestBasicPull(unittest.TestCase):
    def test_single_element(self):
        q = BoundedBatchQueue(8)
        q.put("a")
        self.assertEqual(q.pull_batch(10), ["a"])

    def test_full_batch(self):
        q = BoundedBatchQueue(8)
        for i in range(5):
            q.put(i)
        self.assertEqual(q.pull_batch(5), [0, 1, 2, 3, 4])
        self.assertEqual(len(q), 0)

    def test_fifo_order_across_batches(self):
        q = BoundedBatchQueue(8)
        for i in range(6):
            q.put(i)
        self.assertEqual(q.pull_batch(4), [0, 1, 2, 3])
        self.assertEqual(q.pull_batch(4), [4, 5])


class TestClippingRule(unittest.TestCase):
    """实际拉取数 = min(批量上限, 剩余容量, 已入队数量)。"""

    CASES = [
        # (limit, remaining, enqueued, expected_n)
        (10, 10, 3, 3),    # 已入队数量最小
        (2, 10, 5, 2),     # 批量上限最小
        (10, 4, 8, 4),     # 剩余容量最小
        (3, 3, 3, 3),      # 三者相等
        (1, 100, 100, 1),  # 上限为 1
        (7, 2, 5, 2),      # 剩余容量严格最小
    ]

    def test_min_of_limit_remaining_enqueued(self):
        for limit, remaining, enqueued, expected in self.CASES:
            with self.subTest(limit=limit, remaining=remaining, enqueued=enqueued):
                q = BoundedBatchQueue(128)
                for i in range(enqueued):
                    q.put(i)
                batch = q.pull_batch(limit, remaining)
                self.assertEqual(len(batch), expected)
                self.assertEqual(batch, list(range(expected)))
                self.assertEqual(len(q), enqueued - expected)

    def test_remaining_defaults_to_limit(self):
        q = BoundedBatchQueue(128)
        for i in range(10):
            q.put(i)
        self.assertEqual(len(q.pull_batch(3)), 3)


class TestEmptyQueueWaitsWithoutSpinning(unittest.TestCase):
    def test_pull_blocks_until_put_and_never_spins(self):
        q = BoundedBatchQueue(4)
        holder, t = pull_in_thread(q, 4)

        time.sleep(0.2)  # 给消费者充足时间进入等待
        self.assertTrue(t.is_alive(), "空队列时 pull_batch 必须阻塞等待")
        self.assertNotIn("batch", holder)

        q.put("x")
        t.join(timeout=2)
        self.assertFalse(t.is_alive())
        self.assertEqual(holder["batch"], ["x"])
        # 核心断言：空转次数为零 —— 等待期间没有忙等轮询。
        self.assertEqual(q.spin_count, 0)

    def test_no_spin_under_repeated_empty_waits(self):
        q = BoundedBatchQueue(4)
        results = []
        threads = [
            threading.Thread(target=lambda: results.append(q.pull_batch(2)))
            for _ in range(3)
        ]
        for t in threads:
            t.start()
        time.sleep(0.2)
        for i in range(3):
            q.put(i)
            time.sleep(0.05)
        for t in threads:
            t.join(timeout=2)
            self.assertFalse(t.is_alive())
        self.assertEqual(sorted(b[0] for b in results), [0, 1, 2])
        self.assertEqual(q.spin_count, 0)


class TestCloseSemantics(unittest.TestCase):
    def test_close_with_items_still_drains_then_end_signal(self):
        q = BoundedBatchQueue(8)
        for i in range(3):
            q.put(i)
        q.close()
        # 已入队元素仍要能被取完
        self.assertEqual(q.pull_batch(2), [0, 1])
        self.assertEqual(q.pull_batch(2), [2])
        # 取完之后收到结束信号（空列表）
        self.assertEqual(q.pull_batch(2), [])

    def test_close_wakes_waiting_puller_with_end_signal(self):
        q = BoundedBatchQueue(4)
        holder, t = pull_in_thread(q, 4)
        time.sleep(0.2)
        self.assertTrue(t.is_alive())
        q.close()
        t.join(timeout=2)
        self.assertFalse(t.is_alive(), "关闭后等待者必须被唤醒")
        self.assertEqual(holder["batch"], [])
        self.assertEqual(q.spin_count, 0)

    def test_put_after_close_raises(self):
        q = BoundedBatchQueue(4)
        q.close()
        with self.assertRaises(QueueClosed):
            q.put("x")


class TestBoundedness(unittest.TestCase):
    def test_put_blocks_when_full_and_resumes_after_pull(self):
        q = BoundedBatchQueue(2)
        q.put(1)
        q.put(2)
        done = threading.Event()

        def producer():
            q.put(3)
            done.set()

        t = threading.Thread(target=producer)
        t.start()
        time.sleep(0.2)
        self.assertFalse(done.is_set(), "队列满时 put 必须阻塞")
        self.assertEqual(q.pull_batch(1), [1])
        t.join(timeout=2)
        self.assertTrue(done.is_set())
        self.assertEqual(q.pull_batch(2), [2, 3])


if __name__ == "__main__":
    unittest.main(verbosity=2)
