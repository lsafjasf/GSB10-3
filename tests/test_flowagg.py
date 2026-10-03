"""flowagg 自测：边界判据、合并累加、乱序、复用、内存上界、对拍。"""

import json
import os
import random
import unittest

from flowagg import (FlowAggregator, FlowRecord, aggregate_reference,
                     sessions_to_canonical)
from tests.cases import K1, K2, build_cases, rec

DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "cases.json")
T = 60.0  # 空闲超时


def feed(records, timeout=T, **kw):
    """离线模式：喂完全部记录后 drain，返回规范化会话列表。"""
    agg = FlowAggregator(idle_timeout=timeout, **kw)
    emitted = []
    for r in records:
        emitted.extend(agg.add(r))
    emitted.extend(agg.drain())
    return sorted(emitted,
                  key=lambda d: (d["src_ip"], d["dst_ip"], d["src_port"],
                                 d["dst_port"], d["protocol"], d["start"]))


class TestBoundaryRules(unittest.TestCase):
    """超时边界的明确判据：gap <= t 合并，gap > t 切分。"""

    def test_gap_equal_timeout_merges(self):
        out = feed([rec(K1, 0.0, 10.0, 1, 100),
                    rec(K1, 10.0 + T, 20.0 + T, 2, 200)])  # gap == T
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["packets"], 3)
        self.assertEqual(out[0]["bytes"], 300)
        self.assertEqual(out[0]["start"], 0.0)
        self.assertEqual(out[0]["end"], 20.0 + T)

    def test_gap_above_timeout_splits(self):
        out = feed([rec(K1, 0.0, 10.0, 1, 100),
                    rec(K1, 10.0 + T + 1e-9, 20.0 + T, 2, 200)])  # gap == T + ε
        self.assertEqual(len(out), 2)

    def test_touching_intervals_merge(self):
        out = feed([rec(K1, 0.0, 10.0, 1, 1),
                    rec(K1, 10.0, 20.0, 1, 1)])  # gap == 0
        self.assertEqual(len(out), 1)

    def test_aging_boundary(self):
        agg = FlowAggregator(idle_timeout=T)
        agg.add(rec(K1, 0.0, 10.0, 1, 100))
        # now - end == T：仍活跃
        self.assertEqual(agg.age_out(10.0 + T), [])
        self.assertEqual(agg.active_count, 1)
        # now - end > T：老化输出
        aged = agg.age_out(10.0 + T + 1e-9)
        self.assertEqual(len(aged), 1)
        self.assertEqual(agg.active_count, 0)
        self.assertEqual(agg.sessions_aged, 1)


class TestAccumulation(unittest.TestCase):
    """累计值与逐条求和一致。"""

    def test_sums_match_per_record(self):
        records = [rec(K1, 0.0, 5.0, 2, 100),
                   rec(K1, 6.0, 9.0, 3, 250),
                   rec(K1, 10.0, 12.0, 5, 50)]
        out = feed(records)
        self.assertEqual(len(out), 1)
        s = out[0]
        self.assertEqual(s["packets"], sum(r.packets for r in records))
        self.assertEqual(s["bytes"], sum(r.bytes for r in records))
        self.assertEqual(s["start"], min(r.start for r in records))
        self.assertEqual(s["end"], max(r.end for r in records))
        self.assertEqual(s["records"], len(records))

    def test_single_record_passthrough(self):
        out = feed([rec(K1, 1000.0, 1005.0, 3, 300)])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["packets"], 3)
        self.assertEqual(out[0]["bytes"], 300)
        self.assertEqual(out[0]["start"], 1000.0)
        self.assertEqual(out[0]["end"], 1005.0)


class TestReuseAndOutOfOrder(unittest.TestCase):
    """同一五元组先后复用、乱序到达。"""

    def test_five_tuple_reuse(self):
        records = [rec(K1, 0.0, 5.0, 1, 60),
                   rec(K1, 5.0 + T + 1.0, 8.0 + T, 2, 120)]  # gap > T -> 新会话
        out = feed(records)
        self.assertEqual(len(out), 2)
        self.assertEqual([s["bytes"] for s in out], [60, 120])
        self.assertEqual(sum(s["bytes"] for s in out), 180)

    def test_out_of_order_merges_into_one_session(self):
        # 乱序但两两间隔都在超时内 -> 一个会话
        records = [rec(K1, 30.0, 35.0, 1, 10),
                   rec(K1, 0.0, 5.0, 2, 20),
                   rec(K1, 15.0, 20.0, 3, 30)]
        out = feed(records)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["start"], 0.0)
        self.assertEqual(out[0]["end"], 35.0)
        self.assertEqual(out[0]["packets"], 6)
        self.assertEqual(out[0]["bytes"], 60)

    def test_out_of_order_unmergeable_creates_second_session(self):
        # 乱序到达时与任何活跃会话间隔都 > T -> 新建会话（语义明确，不反悔）
        records = [rec(K1, 0.0, 10.0, 1, 1),
                   rec(K1, 24.0 + T, 26.0 + T, 1, 1),   # 与第一个 gap > T
                   rec(K1, 12.0, 14.0, 1, 1)]           # 迟到，只能并入第一个
        out = feed(records)
        self.assertEqual(len(out), 2)
        self.assertEqual(sum(s["bytes"] for s in out), 3)

    def test_late_record_after_aging_starts_new_session(self):
        agg = FlowAggregator(idle_timeout=T)
        agg.add(rec(K1, 0.0, 10.0, 1, 100))
        aged = agg.age_out(10.0 + T + 1.0)   # 第一个会话老化输出
        agg.add(rec(K1, 5.0, 8.0, 2, 200))   # 迟到记录 -> 新会话
        out = aged + agg.drain()
        self.assertEqual(len(out), 2)
        self.assertEqual(sum(d["bytes"] for d in out), 300)


class TestMemoryBound(unittest.TestCase):
    """活跃会话数与内存的硬上界。"""

    def test_active_count_never_exceeds_cap(self):
        cap = 100
        agg = FlowAggregator(idle_timeout=T, max_sessions=cap)
        peak = 0
        for i in range(5000):
            key = dict(src_ip=f"10.2.{i % 250}.{(i // 250) % 250}",
                       dst_ip="8.8.8.8", src_port=1000 + i % 60000,
                       dst_port=443, protocol=6)
            agg.add(FlowRecord(start=float(i), end=float(i),
                               packets=1, bytes=64, **key))
            peak = max(peak, agg.active_count)
        self.assertLessEqual(peak, cap)
        self.assertEqual(agg.active_count, cap)
        self.assertEqual(agg.sessions_evicted, 5000 - cap)

    def test_evicted_sessions_are_emitted_with_totals(self):
        agg = FlowAggregator(idle_timeout=T, max_sessions=2)
        keys = [dict(src_ip=f"10.3.0.{i}", dst_ip="1.1.1.1",
                     src_port=2000 + i, dst_port=80, protocol=6)
                for i in range(3)]
        emitted = []
        for i, key in enumerate(keys):
            emitted.extend(agg.add(FlowRecord(
                start=float(i * 100), end=float(i * 100) + 1,
                packets=i + 1, bytes=(i + 1) * 10, **key)))
        self.assertEqual(len(emitted), 1)          # 最旧的被逐出
        self.assertEqual(emitted[0]["bytes"], 10)  # 逐出时累计值完整
        self.assertEqual(agg.active_count, 2)

    def test_heap_has_no_stale_entries(self):
        # 大量合并/老化后堆大小恒等于活跃会话数（无懒删除残留）
        agg = FlowAggregator(idle_timeout=T, max_sessions=50)
        for i in range(3000):
            agg.add(rec(K1, float(i), float(i) + 0.5, 1, 1), now=float(i))
        self.assertEqual(agg.active_count, len(agg._heap))
        self.assertLessEqual(agg.active_count, 50)


class TestTimeInjection(unittest.TestCase):
    """时间由测试注入：add(now=...) 触发老化。"""

    def test_add_with_now_ages_sessions(self):
        agg = FlowAggregator(idle_timeout=T)
        done = []
        done.extend(agg.add(rec(K1, 0.0, 10.0, 1, 100), now=0.0))
        done.extend(agg.add(rec(K2, 200.0, 210.0, 2, 200), now=200.0))
        self.assertEqual(len(done), 1)             # K1 在 now=200 时老化
        self.assertEqual(done[0]["bytes"], 100)
        self.assertEqual(agg.active_count, 1)


class TestCrossCheck(unittest.TestCase):
    """与参考实现对拍 + 数据文件回归。"""

    def test_generated_cases_match_reference(self):
        for case in build_cases():
            with self.subTest(case=case["name"]):
                records = [FlowRecord.from_dict(d) for d in case["records"]]
                self.assertEqual(feed(records), case["expected_sessions"])

    def test_cases_json_regression(self):
        with open(DATA_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        for case in data["cases"]:
            with self.subTest(case=case["name"]):
                records = [FlowRecord.from_dict(d) for d in case["records"]]
                self.assertEqual(feed(records, timeout=data["idle_timeout"]),
                                 case["expected_sessions"])

    def test_random_cross_check_with_conservation(self):
        rng = random.Random(7)
        for trial in range(20):
            keys = [dict(src_ip=f"10.9.{trial}.{i}", dst_ip="9.9.9.9",
                         src_port=3000 + i, dst_port=53, protocol=17)
                    for i in range(5)]
            records = []
            clock = 0.0
            for _ in range(200):
                gap = rng.choice([0.0, T / 2, T, T + 0.5, 2 * T])
                clock += gap
                records.append(rec(rng.choice(keys), clock,
                                   clock + rng.uniform(0, 3),
                                   rng.randint(1, 9), rng.randint(1, 999)))
            records.sort(key=lambda r: (r.start, r.end))
            got = feed(records)
            want = sessions_to_canonical(aggregate_reference(records, T))
            self.assertEqual(got, want)
            # 与逐条求和一致的全局守恒断言
            self.assertEqual(sum(s["bytes"] for s in got),
                             sum(r.bytes for r in records))
            self.assertEqual(sum(s["packets"] for s in got),
                             sum(r.packets for r in records))
            self.assertEqual(sum(s["records"] for s in got), len(records))

    def test_shuffled_feed_conserves_totals(self):
        # 乱序喂入：会话划分可不同，但字节/包数总量必须守恒
        rng = random.Random(11)
        records = [rec(K1, float(i * 7), float(i * 7) + 2, 1, i + 1)
                   for i in range(300)]
        shuffled = records[:]
        rng.shuffle(shuffled)
        out = feed(shuffled)
        self.assertEqual(sum(s["bytes"] for s in out),
                         sum(r.bytes for r in records))
        self.assertEqual(sum(s["packets"] for s in out),
                         sum(r.packets for r in records))
        self.assertEqual(sum(s["records"] for s in out), len(records))


if __name__ == "__main__":
    unittest.main()
