"""分片重组自测：python3 -m unittest reassembly.test_reassembler -v"""

import unittest

from reassembly.reassembler import Reassembler


class FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class TestNormalReassembly(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.rx = Reassembler(max_total_bytes=1024, group_timeout=10.0,
                              clock=self.clock)

    def test_in_order(self):
        self.assertIsNone(self.rx.add_fragment("m1", 0, b"hello ", more=True).reassembled)
        self.assertIsNone(self.rx.add_fragment("m1", 6, b"world", more=True).reassembled)
        r = self.rx.add_fragment("m1", 11, b"!", more=False)
        self.assertEqual(r.reassembled, b"hello world!")
        # 完成后缓冲立即释放
        self.assertEqual(self.rx.buffered_bytes, 0)
        self.assertEqual(self.rx.active_groups, 0)

    def test_out_of_order(self):
        self.assertIsNone(self.rx.add_fragment("m2", 11, b"!", more=False).reassembled)
        self.assertIsNone(self.rx.add_fragment("m2", 6, b"world", more=True).reassembled)
        r = self.rx.add_fragment("m2", 0, b"hello ", more=True)
        self.assertEqual(r.reassembled, b"hello world!")

    def test_single_fragment(self):
        r = self.rx.add_fragment("m3", 0, b"one-shot", more=False)
        self.assertEqual(r.reassembled, b"one-shot")

    def test_multiple_ids_isolated(self):
        self.rx.add_fragment("a", 0, b"AA", more=True)
        self.rx.add_fragment("b", 0, b"BB", more=True)
        r = self.rx.add_fragment("a", 2, b"CC", more=False)
        self.assertEqual(r.reassembled, b"AACC")
        self.assertEqual(self.rx.active_groups, 1)  # b 仍在等待


class TestOverlap(unittest.TestCase):
    """重叠处理样例：先到达的数据为准，后到分片的重叠部分被裁掉。"""

    def setUp(self):
        self.clock = FakeClock()
        self.rx = Reassembler(max_total_bytes=1024, group_timeout=10.0,
                              clock=self.clock)

    def test_overlap_first_wins(self):
        # 先到：offset=0 "ABCDEF"
        self.rx.add_fragment("ov", 0, b"ABCDEF", more=True)
        # 后到：offset=3 "XYZW"，其中 offset 3..6 与已到的 "DEF" 重叠，
        # 重叠部分被丢弃，只保留 offset 6 起的 "W"
        r = self.rx.add_fragment("ov", 3, b"XYZW", more=False)
        self.assertEqual(r.reassembled, b"ABCDEFW")  # 不是 "ABCXYZW"

    def test_fully_covered_duplicate(self):
        self.rx.add_fragment("dup", 0, b"ABCDEFGH", more=True)
        r1 = self.rx.add_fragment("dup", 2, b"XYZ", more=True)  # 完全被覆盖
        self.assertEqual(r1.added_bytes, 0)
        r2 = self.rx.add_fragment("dup", 0, b"ABCDEFGH", more=True)  # 完全重复
        self.assertEqual(r2.added_bytes, 0)
        self.assertEqual(self.rx.buffered_bytes, 8)

    def test_overlap_at_tail(self):
        self.rx.add_fragment("tail", 0, b"hello world", more=True)
        r = self.rx.add_fragment("tail", 8, b"rld!!", more=False)
        # offset 8..10 "rld" 与已有 "rld" 重叠被裁，只新增 "!!"
        self.assertEqual(r.added_bytes, 2)
        self.assertEqual(r.reassembled, b"hello world!!")


class TestTimeout(unittest.TestCase):
    """超时判据：now - first_seen >= group_timeout 即丢弃未完成组。"""

    def setUp(self):
        self.clock = FakeClock()
        self.rx = Reassembler(max_total_bytes=1024, group_timeout=10.0,
                              clock=self.clock)

    def test_permanent_missing_then_timeout(self):
        # 永久缺失中间一段：只收到 [0,5) 和 [10,15)，[5,10) 永远不到
        self.rx.add_fragment("lost", 0, b"AAAAA", more=True)
        self.rx.add_fragment("lost", 10, b"BBBBB", more=False)
        self.assertEqual(self.rx.buffered_bytes, 10)
        self.assertEqual(self.rx.holes("lost"), [(5, 10)])

        self.clock.advance(9.9)
        self.assertEqual(self.rx.expire(), [])  # 未到判据
        self.assertEqual(self.rx.active_groups, 1)

        self.clock.advance(0.2)  # now - first_seen = 10.1 >= 10
        records = self.rx.expire()
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.reason, "timeout")
        self.assertEqual(rec.frag_id, "lost")
        self.assertEqual(rec.bytes_freed, 10)  # 资源已释放
        self.assertEqual(self.rx.buffered_bytes, 0)
        self.assertEqual(self.rx.active_groups, 0)
        self.assertIn((5, 10), eval(rec.detail.split("holes=")[1]))

    def test_timeout_checked_on_add(self):
        self.rx.add_fragment("old", 0, b"xx", more=True)
        self.clock.advance(11.0)
        # 下一次 add 会自动先做超时清理
        self.rx.add_fragment("new", 0, b"yy", more=True)
        self.assertEqual(self.rx.active_groups, 1)
        self.assertEqual(self.rx.eviction_log[0].reason, "timeout")
        self.assertEqual(self.rx.eviction_log[0].frag_id, "old")

    def test_completed_group_never_expires(self):
        self.rx.add_fragment("done", 0, b"ok", more=False)
        self.clock.advance(100.0)
        self.assertEqual(self.rx.expire(), [])


class TestCapacityEviction(unittest.TestCase):
    """总量上界：超限按 LRU（last_seen 最早）清退，并输出清退记录。"""

    def setUp(self):
        self.clock = FakeClock()
        self.rx = Reassembler(max_total_bytes=100, group_timeout=1000.0,
                              clock=self.clock)

    def test_lru_eviction_with_records(self):
        self.rx.add_fragment("g1", 0, b"A" * 40, more=True)
        self.clock.advance(1)
        self.rx.add_fragment("g2", 0, b"B" * 40, more=True)
        self.clock.advance(1)
        self.rx.add_fragment("g1", 40, b"A" * 10, more=True)  # g1 刷新活跃度
        self.clock.advance(1)
        # 此刻占用 90B；再来 30B 超限，应清退最久未活动的 g2（40B）
        self.rx.add_fragment("g3", 0, b"C" * 30, more=True)

        self.assertEqual(self.rx.active_groups, 2)
        self.assertEqual(self.rx.buffered_bytes, 80)
        self.assertEqual(len(self.rx.eviction_log), 1)
        rec = self.rx.eviction_log[0]
        self.assertEqual(rec.reason, "capacity")
        self.assertEqual(rec.frag_id, "g2")
        self.assertEqual(rec.bytes_freed, 40)

    def test_reject_oversized_fragment(self):
        r = self.rx.add_fragment("big", 0, b"X" * 101, more=True)
        self.assertFalse(r.accepted)
        self.assertIsNone(r.reassembled)
        self.assertEqual(self.rx.buffered_bytes, 0)
        self.assertEqual(self.rx.eviction_log[-1].reason, "reject")

    def test_evict_multiple_groups_until_fit(self):
        for i in range(4):
            self.rx.add_fragment(f"g{i}", 0, b"D" * 30, more=True)
            self.clock.advance(1)
        # 占用 120 > 100？不会：第 4 次 add 时已触发清退。
        # 验证最终占用 <= 上界且清退记录完整
        self.assertLessEqual(self.rx.buffered_bytes, 100)
        self.assertTrue(all(r.reason == "capacity" for r in self.rx.eviction_log))
        freed = sum(r.bytes_freed for r in self.rx.eviction_log)
        self.assertGreater(freed, 0)


class TestEdgeCases(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.rx = Reassembler(max_total_bytes=64, group_timeout=5.0,
                              clock=self.clock)

    def test_zero_length_final_fragment(self):
        self.rx.add_fragment("z", 0, b"abc", more=True)
        r = self.rx.add_fragment("z", 3, b"", more=False)  # 空末片仅作结束标记
        self.assertEqual(r.reassembled, b"abc")

    def test_empty_message(self):
        r = self.rx.add_fragment("e", 0, b"", more=False)
        self.assertEqual(r.reassembled, b"")

    def test_negative_offset_rejected(self):
        with self.assertRaises(ValueError):
            self.rx.add_fragment("bad", -1, b"x")

    def test_invalid_constructor_args(self):
        with self.assertRaises(ValueError):
            Reassembler(max_total_bytes=0)
        with self.assertRaises(ValueError):
            Reassembler(group_timeout=0)

    def test_exact_cap_boundary(self):
        r = self.rx.add_fragment("fit", 0, b"F" * 64, more=False)
        self.assertTrue(r.accepted)
        self.assertEqual(len(r.reassembled), 64)

    def test_fragment_after_completion_starts_new_group(self):
        self.rx.add_fragment("re", 0, b"ab", more=False)
        r = self.rx.add_fragment("re", 0, b"cd", more=False)
        self.assertEqual(r.reassembled, b"cd")

    def test_many_small_fragments(self):
        rx = Reassembler(max_total_bytes=4096, group_timeout=5.0,
                         clock=self.clock)
        payload = bytes(range(256))
        for i in range(255, -1, -1):  # 完全逆序
            r = rx.add_fragment("rev", i, payload[i:i + 1],
                                more=(i != 255))
        self.assertEqual(r.reassembled, payload)


if __name__ == "__main__":
    unittest.main()
