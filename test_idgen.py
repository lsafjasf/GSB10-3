"""idgen 的自测（仅标准库 unittest）。

运行: python3 -m unittest -v test_idgen
"""

import threading
import unittest

from idgen import (
    EPOCH_MS,
    MAX_NODE_ID,
    MAX_SEQUENCE,
    ClockMovedBackwardsError,
    IdGenerator,
    decode,
)


class FakeClock:
    """可手动推进/回拨的虚拟时钟，线程安全。"""

    def __init__(self, start_ms: int):
        self._now = start_ms
        self._lock = threading.Lock()

    def time_ms(self) -> int:
        with self._lock:
            return self._now

    def set(self, ms: int) -> None:
        with self._lock:
            self._now = ms

    def advance(self, ms: int = 1) -> None:
        with self._lock:
            self._now += ms


def make_gen(clock: FakeClock, node_id: int = 7, **kwargs):
    """构造注入虚拟时钟的发号器；sleep 每次让时钟前进 1ms，使等待可终止。"""
    kwargs.setdefault("epoch_ms", 0)
    return IdGenerator(
        node_id,
        time_ms=clock.time_ms,
        sleep=lambda _s: clock.advance(1),
        **kwargs,
    )


def assert_strictly_increasing(testcase, ids):
    testcase.assertEqual(len(ids), len(set(ids)), "ids must be unique")
    for prev, cur in zip(ids, ids[1:]):
        testcase.assertLess(prev, cur, "ids must be strictly increasing")


class TestStructure(unittest.TestCase):
    def test_layout_and_decode(self):
        clock = FakeClock(1234)
        gen = make_gen(clock, node_id=42)
        value = gen.next_id()
        parts = decode_with_epoch(value, epoch_ms=0)
        self.assertEqual(parts, (1234, 42, 0))
        self.assertGreater(value, 0)  # 符号位恒 0

    def test_field_bounds(self):
        with self.assertRaises(ValueError):
            IdGenerator(-1)
        with self.assertRaises(ValueError):
            IdGenerator(MAX_NODE_ID + 1)

    def test_time_before_epoch_rejected(self):
        clock = FakeClock(100)
        gen = make_gen(clock, epoch_ms=200)
        with self.assertRaises(ValueError):
            gen.next_id()

    def test_cross_node_ordering_same_ms(self):
        # 同一毫秒：节点号大的 ID 更大；不同节点同毫秒也不冲突
        clock = FakeClock(5000)
        gen_a = make_gen(clock, node_id=1)
        gen_b = make_gen(clock, node_id=2)
        ids_a = [gen_a.next_id() for _ in range(100)]
        ids_b = [gen_b.next_id() for _ in range(100)]
        self.assertTrue(set(ids_a).isdisjoint(ids_b))
        self.assertLess(max(ids_a), min(ids_b))


def decode_with_epoch(value, epoch_ms=EPOCH_MS):
    p = decode(value)
    return (p.timestamp_ms - EPOCH_MS + epoch_ms, p.node_id, p.sequence)


class TestSequenceExhaustion(unittest.TestCase):
    def test_same_ms_exhaustion_waits_instead_of_wrapping(self):
        clock = FakeClock(1000)
        sleeps = []
        gen = IdGenerator(
            3,
            time_ms=clock.time_ms,
            sleep=lambda s: (sleeps.append(s), clock.advance(1)),
            epoch_ms=0,
        )
        # 同一毫秒打满 4096 个序号
        first_batch = [gen.next_id() for _ in range(MAX_SEQUENCE + 1)]
        self.assertEqual(len(sleeps), 0, "序号未耗尽时不应等待")
        for i, value in enumerate(first_batch):
            self.assertEqual(decode_with_epoch(value, 0), (1000, 3, i))

        # 第 4097 个：必须等待到下一毫秒，而不是回绕到 seq=0 造成重复
        value = gen.next_id()
        self.assertGreaterEqual(len(sleeps), 1, "序号耗尽时必须发生等待")
        self.assertEqual(decode_with_epoch(value, 0), (1001, 3, 0))
        assert_strictly_increasing(self, first_batch + [value])


class TestClockBackwards(unittest.TestCase):
    def test_large_backward_jump_rejected(self):
        clock = FakeClock(10_000)
        gen = make_gen(clock, max_backward_ms=5)
        first = gen.next_id()
        clock.set(9_900)  # 回拨 100ms，超过容忍阈值
        with self.assertRaises(ClockMovedBackwardsError) as ctx:
            gen.next_id()
        self.assertEqual(ctx.exception.drift_ms, 100)
        # 拒绝后时钟恢复，仍可继续发号且保持单调
        clock.set(10_000)
        second = gen.next_id()
        self.assertLess(first, second)

    def test_small_backward_jump_waits_for_clock(self):
        clock = FakeClock(10_000)
        sleeps = []
        gen = IdGenerator(
            9,
            time_ms=clock.time_ms,
            sleep=lambda s: (sleeps.append(s), clock.advance(1)),
            epoch_ms=0,
            max_backward_ms=5,
        )
        first = gen.next_id()          # t=10000, seq=0
        clock.set(9_998)               # 回拨 2ms（NTP 抖动级别）
        second = gen.next_id()         # 应等待时钟追平到 10000，然后 seq=1
        self.assertGreaterEqual(len(sleeps), 2, "小幅回拨应等待时钟追平")
        self.assertEqual(decode_with_epoch(second, 0), (10_000, 9, 1))
        self.assertLess(first, second)

    def test_zero_tolerance_rejects_any_backward_jump(self):
        clock = FakeClock(10_000)
        gen = make_gen(clock, max_backward_ms=0)
        gen.next_id()
        clock.set(9_999)  # 仅回拨 1ms
        with self.assertRaises(ClockMovedBackwardsError):
            gen.next_id()


class TestSingleNode(unittest.TestCase):
    def test_monotonic_and_unique_10k(self):
        gen = IdGenerator(0)
        ids = [gen.next_id() for _ in range(10_000)]
        assert_strictly_increasing(self, ids)

    def test_thread_safe_single_instance(self):
        gen = IdGenerator(5)
        threads, per_thread, barrier = 8, 2_000, threading.Barrier(8)
        results = [[] for _ in range(threads)]

        def worker(slot):
            barrier.wait()
            results[slot].extend(gen.next_id() for _ in range(per_thread))

        ts = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        all_ids = [v for bucket in results for v in bucket]
        self.assertEqual(len(all_ids), threads * per_thread)
        self.assertEqual(len(set(all_ids)), len(all_ids), "并发下不得产生重复 ID")


class TestMultiNodeConcurrent(unittest.TestCase):
    def test_four_nodes_concurrent(self):
        nodes, per_node = 4, 5_000
        gens = [IdGenerator(i) for i in range(nodes)]
        barrier = threading.Barrier(nodes)
        results = [[] for _ in range(nodes)]

        def worker(slot):
            barrier.wait()  # 尽量让各节点打在同一毫秒
            results[slot].extend(gens[slot].next_id() for _ in range(per_node))

        ts = [threading.Thread(target=worker, args=(i,)) for i in range(nodes)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        for node, bucket in enumerate(results):
            assert_strictly_increasing(self, bucket)  # 每节点严格单调
            for value in bucket:
                self.assertEqual(decode(value).node_id, node)

        merged = sorted(v for bucket in results for v in bucket)
        self.assertEqual(len(merged), nodes * per_node)
        self.assertEqual(len(set(merged)), len(merged), "多节点并发不得冲突")


class TestLargeScale(unittest.TestCase):
    def test_200k_sorted_unique_monotonic(self):
        gen = IdGenerator(1023)
        ids = [gen.next_id() for _ in range(200_000)]
        # 唯一性
        self.assertEqual(len(set(ids)), len(ids))
        # 生成顺序即排序顺序（严格单调）
        self.assertEqual(ids, sorted(ids))
        assert_strictly_increasing(self, ids)
        # 全部可解码回合法字段
        for value in ids[:1000] + ids[-1000:]:
            p = decode(value)
            self.assertEqual(p.node_id, 1023)
            self.assertGreaterEqual(p.timestamp_ms, EPOCH_MS)
            self.assertTrue(0 <= p.sequence <= MAX_SEQUENCE)


if __name__ == "__main__":
    unittest.main()
