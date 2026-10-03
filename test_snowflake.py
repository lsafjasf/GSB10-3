"""SnowflakeGenerator 自测：结构、单调性、唯一性、并发、序号耗尽、时钟回拨。

运行：python3 -m unittest -v   或   python3 test_snowflake.py
"""

import threading
import unittest

from snowflake import (
    ClockMovedBackwardsError,
    SnowflakeGenerator,
    TimestampOverflowError,
)

BASE_MS = 1_700_000_000_000  # 固定的假时钟起点，测试可复现


class FakeClock:
    """可注入的假时钟：线程安全；sleep 时拨快 1ms，模拟时间流逝。"""

    def __init__(self, start_ms: int = BASE_MS):
        self._ms = start_ms
        self._lock = threading.Lock()
        self.sleep_calls = 0

    def time(self) -> float:
        with self._lock:
            return self._ms / 1000.0

    def sleep(self, _seconds: float) -> None:
        with self._lock:
            self.sleep_calls += 1
            self._ms += 1  # 每睡一次前进 1ms

    def now_ms(self) -> int:
        with self._lock:
            return self._ms

    def set_ms(self, ms: int) -> None:
        with self._lock:
            self._ms = ms


class TestStructure(unittest.TestCase):
    """ID 结构必须包含时间、节点、序号三段。"""

    def test_fields_roundtrip(self):
        clock = FakeClock()
        gen = SnowflakeGenerator(42, time_fn=clock.time, sleep_fn=clock.sleep)
        id1 = gen.next_id()
        id2 = gen.next_id()
        d1, d2 = SnowflakeGenerator.decode(id1), SnowflakeGenerator.decode(id2)
        self.assertEqual(d1.timestamp_ms, BASE_MS)
        self.assertEqual(d1.node_id, 42)
        self.assertEqual(d1.sequence, 0)
        self.assertEqual(d2.sequence, 1)  # 同毫秒内序号递增
        self.assertGreater(id1, 0)        # 最高位恒 0，是正整数

    def test_invalid_node_id_rejected(self):
        for bad in (-1, 1024, 10**6):
            with self.assertRaises(ValueError):
                SnowflakeGenerator(bad)

    def test_timestamp_overflow_rejected(self):
        clock = FakeClock()
        gen = SnowflakeGenerator(0, time_fn=clock.time, sleep_fn=clock.sleep)
        clock.set_ms(gen.epoch_ms + SnowflakeGenerator.MAX_TIMESTAMP + 1)
        with self.assertRaises(TimestampOverflowError):
            gen.next_id()


class TestMonotonicityAndUniqueness(unittest.TestCase):
    """大规模生成后断言严格单调（天然有序）且全局唯一。"""

    def test_single_node_large_scale(self):
        clock = FakeClock()
        gen = SnowflakeGenerator(7, time_fn=clock.time, sleep_fn=clock.sleep)
        n = 200_000  # 远超单毫秒 4096 的序号容量，必然多次触发等待
        ids = [gen.next_id() for _ in range(n)]
        self.assertEqual(len(set(ids)), n, "存在重复 ID")
        self.assertEqual(ids, sorted(ids), "ID 未严格递增")
        self.assertTrue(all(b > a for a, b in zip(ids, ids[1:])),
                        "ID 非严格单调")

    def test_multi_node_concurrent(self):
        """多节点并发：全局唯一；每个节点各自严格单调。"""
        clock = FakeClock()  # 多线程共享同一假时钟
        nodes, per_node = 4, 5_000
        results = {nid: [] for nid in range(nodes)}
        gens = [
            SnowflakeGenerator(nid, time_fn=clock.time, sleep_fn=clock.sleep)
            for nid in range(nodes)
        ]

        def worker(nid, gen):
            out = results[nid]
            for _ in range(per_node):
                out.append(gen.next_id())

        threads = [
            threading.Thread(target=worker, args=(nid, gens[nid]))
            for nid in range(nodes)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        all_ids = [i for ids in results.values() for i in ids]
        self.assertEqual(len(set(all_ids)), nodes * per_node,
                         "多节点并发产生重复 ID")
        for nid, ids in results.items():
            self.assertEqual(ids, sorted(ids), f"节点 {nid} 内非严格递增")
            self.assertTrue(
                all(SnowflakeGenerator.decode(i).node_id == nid for i in ids),
                f"节点 {nid} 的 ID 混入了其他节点号",
            )

    def test_same_generator_thread_safe(self):
        """同一发号器被多线程并发调用：结果唯一且集合大小正确。"""
        clock = FakeClock()
        gen = SnowflakeGenerator(3, time_fn=clock.time, sleep_fn=clock.sleep)
        threads_n, per_thread = 8, 2_000
        bag, bag_lock = [], threading.Lock()

        def worker():
            local = [gen.next_id() for _ in range(per_thread)]
            with bag_lock:
                bag.extend(local)

        threads = [threading.Thread(target=worker) for _ in range(threads_n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(set(bag)), threads_n * per_thread)


class TestSequenceExhaustion(unittest.TestCase):
    """同一毫秒序号耗尽时必须等待下一毫秒，而不是回绕。"""

    def test_waits_instead_of_wrapping(self):
        clock = FakeClock()
        gen = SnowflakeGenerator(0, time_fn=clock.time, sleep_fn=clock.sleep)
        capacity = SnowflakeGenerator.MAX_SEQUENCE + 1  # 4096
        ids = [gen.next_id() for _ in range(capacity + 2)]

        # 前 4096 个落在起始毫秒，序号 0..4095
        for seq, id_ in enumerate(ids[:capacity]):
            d = SnowflakeGenerator.decode(id_)
            self.assertEqual((d.timestamp_ms, d.sequence), (BASE_MS, seq))

        # 第 4097 个：发生了等待，进入下一毫秒且序号从 0 重新开始（未回绕）
        self.assertGreater(clock.sleep_calls, 0, "序号耗尽时未发生等待")
        d = SnowflakeGenerator.decode(ids[capacity])
        self.assertEqual(d.timestamp_ms, BASE_MS + 1)
        self.assertEqual(d.sequence, 0)
        self.assertEqual(ids, sorted(ids))


class TestClockRollback(unittest.TestCase):
    """时钟回拨：默认拒绝；配置容忍度时小幅回拨等待追平。"""

    def test_rollback_rejected_by_default(self):
        clock = FakeClock()
        gen = SnowflakeGenerator(0, time_fn=clock.time, sleep_fn=clock.sleep)
        gen.next_id()
        clock.set_ms(BASE_MS - 10)  # 回拨 10ms
        with self.assertRaises(ClockMovedBackwardsError):
            gen.next_id()
        # 时钟恢复后仍可正常发号，且保持单调
        clock.set_ms(BASE_MS)
        self.assertGreater(gen.next_id(), 0)

    def test_small_rollback_waits_when_tolerated(self):
        clock = FakeClock()
        gen = SnowflakeGenerator(
            0, time_fn=clock.time, sleep_fn=clock.sleep,
            rollback_tolerance_ms=100,
        )
        first = gen.next_id()
        clock.set_ms(BASE_MS - 50)  # 回拨 50ms，在容忍范围内
        second = gen.next_id()      # 等待时钟追平（假时钟每次 sleep 前进 1ms）
        self.assertGreater(clock.sleep_calls, 0, "小幅回拨未发生等待")
        self.assertGreater(second, first, "回拨等待后破坏了单调性")

    def test_large_rollback_rejected_even_with_tolerance(self):
        clock = FakeClock()
        gen = SnowflakeGenerator(
            0, time_fn=clock.time, sleep_fn=clock.sleep,
            rollback_tolerance_ms=100,
        )
        gen.next_id()
        clock.set_ms(BASE_MS - 5_000)  # 回拨 5s，超出容忍
        with self.assertRaises(ClockMovedBackwardsError):
            gen.next_id()


if __name__ == "__main__":
    unittest.main(verbosity=2)
