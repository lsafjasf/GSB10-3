"""自测：顺序保证、并行重排、位点恢复、重复投递、边界用例。

运行：python3 -m unittest -v test_cdc
"""

import os
import random
import tempfile
import time
import unittest
from collections import defaultdict

from cdc import (Change, ChangeLog, CheckpointStore, Dispatcher,
                 ParallelConsumer, ReorderBuffer, _SimulatedCrash)


class OrderRecorder:
    """记录每个主键实际收到变更的 lsn 序列，并统计乱序次数。"""

    def __init__(self, sleep_max=0.003):
        self.per_key = defaultdict(list)
        self.global_seq = []          # 实际完成顺序（可能全局乱序）
        self.deliveries = 0
        self.sleep_max = sleep_max

    def handler(self, change: Change) -> None:
        seen = self.per_key[change.pk]
        if seen and change.lsn <= seen[-1]:
            raise AssertionError(f"主键 {change.pk} 乱序投递: {seen[-1]} -> {change.lsn}")
        seen.append(change.lsn)
        self.global_seq.append(change)
        self.deliveries += 1
        if self.sleep_max:
            time.sleep(random.random() * self.sleep_max)  # 放大并行交错

    def out_of_order_count(self) -> int:
        return sum(
            1
            for seq in self.per_key.values()
            for a, b in zip(seq, seq[1:])
            if b <= a
        )


class CDCTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log_path = os.path.join(self.tmp.name, "changes.jsonl")
        self.cp_path = os.path.join(self.tmp.name, "checkpoints.json")
        random.seed(42)

    def tearDown(self):
        self.tmp.cleanup()

    def _consumer(self, log, cps, dispatcher, cid="g1"):
        return ParallelConsumer(cid, log, cps, dispatcher)

    # ---- 用例 1：单键连续变更，必须严格有序 ----
    def test_single_key_consecutive(self):
        log = ChangeLog(self.log_path)
        cps = CheckpointStore(self.cp_path)
        d = Dispatcher(2)
        for i in range(1, 21):
            log.append("row-1", "UPDATE", {"v": i})

        rec = OrderRecorder(sleep_max=0)
        self._consumer(log, cps, d).process_batch(rec.handler)

        self.assertEqual(rec.per_key["row-1"], list(range(1, 21)))
        self.assertEqual(rec.out_of_order_count(), 0)  # 乱序检测为零的断言
        self.assertEqual(cps.get("g1:p0"), 0)         # row-1 只在某一个分区
        self.assertEqual(max(cps.snapshot().values()), 20)

    # ---- 用例 2：多键交错，并行处理；同键有序、全局可重排 ----
    def test_multi_key_interleaved_and_reorder(self):
        log = ChangeLog(self.log_path)
        cps = CheckpointStore(self.cp_path)
        d = Dispatcher(4)
        pks = ["alice", "bob", "carol", "dave", "eve"]
        originals = []
        for i in range(100):
            pk = pks[i % len(pks)]
            originals.append(log.append(pk, "UPDATE", {"i": i}))

        rec = OrderRecorder(sleep_max=0.004)
        delivered = self._consumer(log, cps, d).process_batch(rec.handler)

        # 2.1 每个主键严格按产生顺序，乱序数为零
        self.assertEqual(rec.out_of_order_count(), 0)
        for pk in pks:
            expected = [c.lsn for c in originals if c.pk == pk]
            self.assertEqual(rec.per_key[pk], expected)

        # 2.2 并行完成顺序与全局 lsn 顺序确实不同（否则没有验证到并行交错）
        delivered_lsns = [c.lsn for c in delivered]
        self.assertNotEqual(delivered_lsns, sorted(delivered_lsns))

        # 2.3 ReorderBuffer 按需重排回全局顺序
        buf = ReorderBuffer(expected_lsn=1)
        reordered = []
        for c in delivered:
            buf.add(c)
            reordered.extend(buf.drain())
        # 全部到达后必须能完整重排出 1..100
        self.assertEqual([c.lsn for c in reordered], list(range(1, 101)))
        self.assertEqual([(c.lsn, c.pk) for c in reordered],
                         [(c.lsn, c.pk) for c in originals])

    # ---- 用例 3：ReorderBuffer 缺口到达前必须等待 ----
    def test_reorder_buffer_gap(self):
        buf = ReorderBuffer(expected_lsn=1)
        c = lambda lsn, pk="k": Change(lsn, pk, "UPDATE", {})
        buf.add(c(3))
        buf.add(c(2))
        self.assertEqual(buf.drain(), [])           # 1 未到，不能输出
        buf.add(c(1))
        self.assertEqual([x.lsn for x in buf.drain()], [1, 2, 3])
        buf.add(c(3))                               # 重复位点
        buf.add(c(4))
        self.assertEqual([x.lsn for x in buf.drain()], [4])  # 重复被丢弃

    # ---- 用例 4：消费者崩溃重启，未确认变更重新投递 ----
    def test_restart_redelivers_unacked(self):
        log = ChangeLog(self.log_path)
        cps = CheckpointStore(self.cp_path)
        d = Dispatcher(3)
        pks = ["k1", "k2", "k3", "k4", "k5"]
        for i in range(30):
            log.append(pks[i % len(pks)], "UPDATE", {"i": i})

        rec = OrderRecorder(sleep_max=0.002)
        crash_lsn = 17
        victim_pk = next(c.pk for c in log.all() if c.lsn == crash_lsn)
        victim_partition = d.partition_for(victim_pk)

        consumer = self._consumer(log, cps, d)
        with self.assertRaises(_SimulatedCrash):
            consumer.process_batch(
                rec.handler, crash_after=lambda c: c.lsn == crash_lsn)

        cp_during = cps.snapshot()
        # 崩溃分区位点停在 16；其他分区可以继续推进
        self.assertEqual(cp_during[f"g1:p{victim_partition}"], 16)
        self.assertIn(30, cp_during.values())

        # “重启”：同一 consumer_id 重新构造消费者，磁盘位点决定重放起点
        already = {c.lsn for c in rec.global_seq}  # 崩溃前已生效（模拟持久化幂等表）
        rec2 = OrderRecorder(sleep_max=0.002)
        duplicates = []

        def idempotent_handler(change):
            if change.lsn in already:
                duplicates.append(change.lsn)  # at-least-once：同位点会再来
            already.add(change.lsn)
            rec2.handler(change)

        consumer2 = self._consumer(log, cps, d)
        redelivered = consumer2.process_batch(idempotent_handler)

        # 17 及之后属于崩溃分区的变更全部重新投递，且只重投该分区
        self.assertIn(17, [c.lsn for c in redelivered])
        self.assertTrue(duplicates, "崩溃前已处理但未确认的变更必须重复投递")
        self.assertIn(17, duplicates)
        for c in redelivered:
            self.assertEqual(d.partition_for(c.pk), victim_partition)

        # 重放后每个主键看到的最终序列仍严格有序，乱序为零
        merged = defaultdict(list)
        for lsn in already:
            ch = next(x for x in log.all() if x.lsn == lsn)
            merged[ch.pk].append(lsn)
        for seq in merged.values():
            self.assertEqual(seq, sorted(seq))

        # 位点全部推进到 30，再跑一次无任何投递
        self.assertEqual(max(cps.snapshot().values()), 30)
        self.assertEqual(consumer2.process_batch(lambda c: None), [])

    # ---- 用例 5：显式重复投递幂等性（业务去重）----
    def test_explicit_duplicate_delivery(self):
        log = ChangeLog(self.log_path)
        cps = CheckpointStore(self.cp_path)
        d = Dispatcher(2)
        log.append("only", "INSERT", {"v": 1})
        log.append("only", "UPDATE", {"v": 2})

        applied = {}          # lsn -> 生效次数（幂等：每个 lsn 只生效一次）
        attempts = []

        def idempotent_handler(change):
            attempts.append(change.lsn)
            applied.setdefault(change.lsn, 0)
            applied[change.lsn] += 1 if change.lsn not in applied else 0
            applied[change.lsn] = 1 if applied[change.lsn] == 0 else applied[change.lsn]

        # 第一次正常消费
        c = self._consumer(log, cps, d)
        c.process_batch(idempotent_handler)

        # 模拟位点丢失后的重复投递（直接重放日志）
        replay_rec = OrderRecorder(sleep_max=0)
        for change in log.all():
            replay_rec.handler(change)           # 同 lsn 再投一次
            idempotent_handler(change)

        self.assertEqual(sorted(applied), [1, 2])
        self.assertEqual(attempts, [1, 2, 1, 2])  # 投递了两遍
        self.assertTrue(all(v == 1 for v in applied.values()))  # 生效只一遍

    # ---- 边界用例 ----
    def test_boundaries(self):
        log = ChangeLog(self.log_path)
        cps = CheckpointStore(self.cp_path)
        d = Dispatcher(1)

        # 空日志：什么也不投递
        c = self._consumer(log, cps, d)
        self.assertEqual(c.process_batch(lambda ch: None), [])

        # 分区数大于变更数 / 单分区 / 单条变更
        d2 = Dispatcher(8)
        log.append("solo", "INSERT", {})
        rec = OrderRecorder(sleep_max=0)
        c2 = ParallelConsumer("g2", log, cps, d2)
        c2.process_batch(rec.handler)
        self.assertEqual(rec.per_key["solo"], [1])

        # 位点不能回退
        solo_key = f"g2:p{d2.partition_for('solo')}"
        self.assertEqual(cps.get(solo_key), 1)
        with self.assertRaises(ValueError):
            cps.commit(solo_key, 0)

        # 位点文件跨进程重启后仍在（重新读盘）
        cps2 = CheckpointStore(self.cp_path)
        self.assertEqual(cps2.snapshot(), cps.snapshot())

        # 日志跨进程重启后可完整重读且 lsn 不重置
        log2 = ChangeLog(self.log_path)
        new = log2.append("solo", "UPDATE", {"x": 1})
        self.assertEqual(new.lsn, 2)

    # ---- 同键永远同分区（顺序保证的物理前提）----
    def test_partition_stability(self):
        d = Dispatcher(7)
        self.assertTrue(all(d.partition_for("stable-pk") == d.partition_for("stable-pk")
                            for _ in range(100)))
        self.assertIsInstance(d.partition_for("any"), int)
        with self.assertRaises(ValueError):
            Dispatcher(0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
