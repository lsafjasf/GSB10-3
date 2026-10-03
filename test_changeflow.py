"""changeflow 自测：顺序断言、并行、重启重投、重复投递、位点、边界用例。

运行：python3 -m unittest -v test_changeflow
"""

import os
import shutil
import tempfile
import threading
import time
import unittest

from changeflow import (
    Change,
    ChangeLog,
    Dispatcher,
    OffsetStore,
    assert_per_key_order,
    global_reorder,
)


class IdempotentConsumer:
    """幂等消费者：重复投递同一 seq 只生效一次，但每次都确认。"""

    def __init__(self, fail_sleep: float = 0.0):
        self.applied = {}      # key -> [seq,...]，去重后的生效顺序
        self.attempts = {}     # seq -> 投递次数
        self.lock = threading.Lock()
        self.fail_sleep = fail_sleep

    def __call__(self, change, ack):
        with self.lock:
            self.attempts[change.seq] = self.attempts.get(change.seq, 0) + 1
            first = change.seq not in self.applied.setdefault(change.key, [])
            if first:
                self.applied[change.key].append(change.seq)
            attempt = self.attempts[change.seq]
        if self.fail_sleep and attempt == 1:
            # 模拟处理卡住/崩溃：第一次投递不确认，等超时重投
            time.sleep(self.fail_sleep)
            return
        ack(change.seq)


class ChangeFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cf-test-")
        self.log = ChangeLog(os.path.join(self.tmp, "changes.log"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # 1) 单键连续变更：必须严格按 seq 顺序投递 --------------------------------

    def test_single_key_consecutive(self):
        for i in range(10):
            self.log.append("k1", "update", {"v": i})
        consumer = IdempotentConsumer()
        offs = OffsetStore(self.tmp)
        d = Dispatcher(self.log, offs, consumer, delivery_timeout=1.0)
        d.start()
        self.assertTrue(d.join_drained(5))
        d.stop()

        self.assertEqual(consumer.applied["k1"], list(range(1, 11)))
        self.assertEqual(d.delivered_for("k1"), list(range(1, 11)))
        # 乱序检测为零的断言
        assert_per_key_order(d.delivery_map(), {"k1": list(range(1, 11))})
        self.assertEqual(offs.watermark, 10)

    # 2) 多键交错：不同键并行、各自保序，且可重排为全局顺序 -----------------

    def test_multi_key_interleaved_parallel(self):
        keys = ["A", "B", "C", "D"]
        n = 3
        for i in range(n):
            for key in keys:  # 产生顺序在键之间交错：A,B,C,D,A,B,C,D,...
                self.log.append(key, "update", {"round": i})

        windows = {}
        wlock = threading.Lock()
        processed = []
        plock = threading.Lock()

        def handler(change, ack):
            with wlock:
                windows.setdefault(change.key, [time.monotonic(), None])
                start = windows[change.key][0]
            time.sleep(0.12)  # 放大并行窗口
            with wlock:
                windows[change.key][1] = time.monotonic()
            with plock:
                processed.append((change.seq, change))
            ack(change.seq)

        offs = OffsetStore(self.tmp)
        d = Dispatcher(self.log, offs, handler, workers=4, delivery_timeout=2.0)
        t0 = time.monotonic()
        d.start()
        self.assertTrue(d.join_drained(10))
        elapsed = time.monotonic() - t0
        d.stop()

        expected = {k: [s for s in range(1, n * len(keys) + 1)
                        if (s - 1) % len(keys) == keys.index(k)] for k in keys}
        assert_per_key_order(d.delivery_map(), expected)

        # 全局重排：并行完成顺序是乱的，但能按需还原成全局 seq 顺序
        self.assertNotEqual([p[0] for p in processed],
                            list(range(1, n * len(keys) + 1)))
        reordered = global_reorder(p for _, p in processed)
        self.assertEqual([c.seq for c in reordered],
                         list(range(1, n * len(keys) + 1)))

        # 确实并行：串行至少 n*len(keys)*0.12=1.44s，并行应明显更快，且有时间重叠
        self.assertLess(elapsed, n * len(keys) * 0.12 * 0.6)
        intervals = [(v[0], v[1]) for v in windows.values() if v[1]]
        overlap = any(max(a0, b0) < min(a1, b1)
                      for i, (a0, a1) in enumerate(intervals)
                      for b0, b1 in intervals[i + 1:])
        self.assertTrue(overlap, "不同主键的处理时间窗口应存在重叠（并行）")

    # 3) 消费者重启：未确认重投、已确认不重投、位点持久化 --------------------

    def test_consumer_restart(self):
        keys = ["A", "B", "C"]
        for i in range(2):  # seq: 1A 2B 3C 4A 5B 6C
            for key in keys:
                self.log.append(key, "update", {"round": i})

        release = threading.Event()

        def first_round_acks(change, ack):
            if change.seq <= 3:
                ack(change.seq)        # 前 3 条确认（水位线推进到 3）
            else:
                release.wait(2.0)      # 后 3 条在途但未确认 -> 模拟崩溃丢失

        offs = OffsetStore(self.tmp)
        d1 = Dispatcher(self.log, offs, first_round_acks,
                        workers=4, auto_ack=False, delivery_timeout=5.0)
        d1.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            dm = d1.delivery_map()
            if all(len(dm.get(k, ())) == 2 for k in keys):
                break
            time.sleep(0.01)
        d1.crash_stop()                # 崩溃：不等确认
        release.set()                  # 放行挂住的 worker 线程
        self.assertEqual(offs.watermark, 3)
        with open(os.path.join(self.tmp, "watermark.json")) as fh:
            self.assertIn('"watermark": 3', fh.read())

        # 重启：同一份日志 + 同一份位点记录，新建分发器
        offs2 = OffsetStore(self.tmp)  # 从磁盘恢复位点
        self.assertEqual(offs2.watermark, 3)
        consumer2 = IdempotentConsumer()
        d2 = Dispatcher(self.log, offs2, consumer2, delivery_timeout=1.0)
        d2.start()
        self.assertTrue(d2.join_drained(5))
        d2.stop()

        # 只有 4/5/6 被重新投递，且各一次；1/2/3 不重复
        self.assertEqual({k: d2.delivered_for(k) for k in keys},
                         {"A": [4], "B": [5], "C": [6]})
        combined = {
            k: [s for i in (d1.delivered_for(k), d2.delivered_for(k)) for s in i]
            for k in keys
        }
        expected = {"A": [1, 4], "B": [2, 5], "C": [3, 6]}
        assert_per_key_order(combined, expected)  # 跨重启乱序检测仍为 0
        self.assertEqual(consumer2.applied, {"A": [4], "B": [5], "C": [6]})
        self.assertEqual(offs2.watermark, 6)

    # 4) 变更被重复投递：超时重投 + 幂等消费 -------------------------------

    def test_duplicate_delivery_after_timeout(self):
        self.log.append("k1", "update", {"v": 1})
        self.log.append("k1", "update", {"v": 2})
        # delivery_timeout=0.2：第一次投递 sleep 0.5 不确认 -> 超时重投
        consumer = IdempotentConsumer(fail_sleep=0.5)
        offs = OffsetStore(self.tmp)
        d = Dispatcher(self.log, offs, consumer, workers=2,
                       auto_ack=False, delivery_timeout=0.2, tick=0.02)
        d.start()
        self.assertTrue(d.join_drained(8))
        d.stop()

        deliveries = d.delivered_for("k1")
        self.assertEqual(deliveries, [1, 1, 2, 2])   # 两条各重投一次
        self.assertEqual(consumer.applied["k1"], [1, 2])  # 生效仍严格一次且有序
        self.assertEqual(consumer.attempts[1], 2)
        self.assertEqual(consumer.attempts[2], 2)
        assert_per_key_order(d.delivery_map(), {"k1": [1, 2]})

    # 5) 位点记录：洞、水位线推进、重启恢复、压缩 ---------------------------

    def test_offset_watermark_hole_and_compaction(self):
        offs = OffsetStore(self.tmp, compact_after=3)
        offs.ack(1)
        self.assertEqual(offs.watermark, 1)
        offs.ack(3)  # 洞：3 已确认但 2 未确认，水位线不动
        self.assertEqual(offs.watermark, 1)
        self.assertEqual(offs.acked_ahead(), {3})
        offs.ack(2)  # 洞补齐，水位线连续推进到 3（触发压缩）
        self.assertEqual(offs.watermark, 3)
        self.assertEqual(offs.acked_ahead(), set())

        restored = OffsetStore(self.tmp)  # 模拟重启，从磁盘重建
        self.assertEqual(restored.watermark, 3)
        self.assertTrue(restored.is_acked(1))
        self.assertTrue(restored.is_acked(3))
        self.assertFalse(restored.is_acked(4))
        restored.ack(4)
        self.assertEqual(restored.watermark, 4)
        restored.ack(4)  # 重复确认幂等
        self.assertEqual(restored.watermark, 4)

    # 6) 边界用例 ----------------------------------------------------------

    def test_empty_log(self):
        offs = OffsetStore(self.tmp)
        consumer = IdempotentConsumer()
        d = Dispatcher(self.log, offs, consumer)
        d.start()
        self.assertTrue(d.join_drained(2))
        d.stop()
        self.assertEqual(d.delivery_map(), {})
        self.assertEqual(offs.watermark, 0)

    def test_handler_error_redelivers(self):
        self.log.append("k9", "update", {})
        state = {"calls": 0, "fail_until": 2}
        done = threading.Event()

        def handler(change, ack):
            state["calls"] += 1
            if state["calls"] < state["fail_until"]:
                raise RuntimeError("boom")  # 抛异常 -> 稍后重投
            ack(change.seq)
            done.set()

        offs = OffsetStore(self.tmp)
        d = Dispatcher(self.log, offs, handler, auto_ack=False,
                       delivery_timeout=5.0, tick=0.01)
        d.start()
        self.assertTrue(done.wait(5))
        d.stop()
        self.assertEqual(state["calls"], 2)
        self.assertEqual(d.delivered_for("k9"), [1, 1])
        self.assertEqual(offs.watermark, 1)

    # 7) 顺序断言自身的正确性：能抓到乱序 -----------------------------------

    def test_assertion_detects_disorder(self):
        with self.assertRaises(AssertionError):
            assert_per_key_order({"k": [1, 3, 2]}, {"k": [1, 2, 3]})
        with self.assertRaises(AssertionError):
            assert_per_key_order({"k": [1, 2]}, {"k": [1, 2, 3]})  # 漏投
        with self.assertRaises(AssertionError):
            assert_per_key_order({"k": [1, 2]}, {"k": [1]})        # 多投
        # 合法的重复投递不应误报
        assert_per_key_order({"k": [1, 1, 2, 2, 3]}, {"k": [1, 2, 3]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
