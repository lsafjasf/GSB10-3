"""ipsetx 自测：合并规则（相邻/包含/相同）、跨版本隔离、查询与边界用例。

运行：python3 -m unittest test_ipsetx -v
"""

import unittest

from ipsetx import IPSet, MISS, merge_segments


def merged_map(entries):
    """合并后返回 {cidr: priority}，便于断言。"""
    return {s.cidr: s.priority for s in merge_segments(entries)}


class TestMergeRules(unittest.TestCase):
    def test_single_segment(self):
        self.assertEqual(merged_map([("10.0.0.0/24", 5)]), {"10.0.0.0/24": 5})

    def test_adjacent_aligned_merge_to_single_cidr(self):
        # 对齐相邻：两个 /24 合成一个 /23
        self.assertEqual(
            merged_map([("10.0.0.0/24", 5), ("10.0.1.0/24", 5)]),
            {"10.0.0.0/23": 5},
        )

    def test_adjacent_unaligned_covered_by_minimal_cidrs(self):
        # 非对齐相邻：无法聚成单个 CIDR，用最少 CIDR 完整覆盖，不多不少
        result = merged_map([("10.0.1.0/24", 1), ("10.0.2.0/24", 1)])
        self.assertEqual(result, {"10.0.1.0/24": 1, "10.0.2.0/24": 1})
        result = merged_map([("10.0.0.0/24", 1), ("10.0.1.0/24", 1), ("10.0.2.0/24", 1)])
        self.assertEqual(result, {"10.0.0.0/23": 1, "10.0.2.0/24": 1})

    def test_adjacent_chain_ipv6(self):
        self.assertEqual(
            merged_map([("2001:db8::/64", 2), ("2001:db8:0:1::/64", 2)]),
            {"2001:db8::/63": 2},
        )

    def test_containment_absorbed(self):
        self.assertEqual(
            merged_map([("10.0.0.0/8", 9), ("10.1.2.0/24", 3)]),
            {"10.0.0.0/8": 3},  # 合并段保留最高优先级（最小数值）
        )

    def test_identical_deduplicated(self):
        self.assertEqual(
            merged_map([("10.0.0.0/24", 7), ("10.0.0.0/24", 2), ("10.0.0.0/24", 9)]),
            {"10.0.0.0/24": 2},
        )

    def test_partial_overlap(self):
        self.assertEqual(
            merged_map([("10.0.0.0/24", 4), ("10.0.0.128/25", 4)]),
            {"10.0.0.0/24": 4},
        )

    def test_disjoint_segments_kept_separate(self):
        result = merged_map([("10.0.0.0/24", 1), ("10.0.2.0/24", 2)])
        self.assertEqual(result, {"10.0.0.0/24": 1, "10.0.2.0/24": 2})

    def test_host_bits_normalized(self):
        # 带主机位的写法归一化为网段
        self.assertEqual(merged_map([("10.0.0.137/24", 5)]), {"10.0.0.0/24": 5})

    def test_empty_input(self):
        self.assertEqual(merged_map([]), {})
        self.assertEqual(len(IPSet()), 0)


class TestCrossVersion(unittest.TestCase):
    def test_mixed_input_not_mixed_together(self):
        # v4 与 v6 混合输入：分别合并，绝不互相混算
        ipset = IPSet([
            ("10.0.0.0/24", 5),
            ("10.0.1.0/24", 5),
            ("2001:db8::/64", 7),
            ("2001:db8:0:1::/64", 7),
        ])
        self.assertEqual([s.cidr for s in ipset.segments_v4()], ["10.0.0.0/23"])
        self.assertEqual([s.cidr for s in ipset.segments_v6()], ["2001:db8::/63"])
        self.assertEqual(ipset.stats(), {"ipv4_segments": 1, "ipv6_segments": 1, "total": 2})

    def test_v4_mapped_v6_is_ipv6(self):
        # ::ffff:10.0.0.1 是 IPv6 地址，必须走 v6 索引而不是 v4
        ipset = IPSet([("10.0.0.0/24", 1)])
        result = ipset.query("::ffff:10.0.0.1")
        self.assertFalse(result.hit)
        self.assertEqual(result.version, 6)
        ipset6 = IPSet([("::ffff:10.0.0.0/104", 1)])
        self.assertTrue(ipset6.query("::ffff:10.0.0.1").hit)

    def test_full_space_both_versions(self):
        ipset = IPSet([("0.0.0.0/0", 100), ("::/0", 200)])
        self.assertTrue(ipset.query("255.255.255.255").hit)
        self.assertTrue(ipset.query("ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff").hit)
        self.assertEqual(ipset.query("1.2.3.4").priority, 100)
        self.assertEqual(ipset.query("::1").priority, 200)


class TestQuery(unittest.TestCase):
    def setUp(self):
        self.ipset = IPSet([
            ("10.0.0.0/24", 5),
            ("10.0.1.0/24", 3),   # 与上一段相邻 -> 合并为 10.0.0.0/23, 优先级取 3
            ("192.168.0.0/16", 8),
            ("2001:db8::/32", 6),
        ])

    def test_hit_returns_segment_and_priority(self):
        r = self.ipset.query("10.0.0.7")
        self.assertTrue(r.hit)
        self.assertEqual(r.segment, "10.0.0.0/23")
        self.assertEqual(r.priority, 3)
        self.assertEqual(r.version, 4)

    def test_boundary_addresses(self):
        self.assertTrue(self.ipset.query("10.0.0.0").hit)        # 段首
        self.assertTrue(self.ipset.query("10.0.1.255").hit)      # 段尾
        self.assertFalse(self.ipset.query("10.0.2.0").hit)       # 段尾 +1
        self.assertFalse(self.ipset.query("9.255.255.255").hit)  # 段首 -1

    def test_miss_is_explicit(self):
        r = self.ipset.query("8.8.8.8")
        self.assertFalse(r.hit)
        self.assertIsNone(r.segment)
        self.assertIsNone(r.priority)
        self.assertEqual(r.to_dict(), {"hit": False, "result": MISS, "version": 4})

    def test_ipv6_hit_and_miss(self):
        self.assertEqual(self.ipset.query("2001:db8::1").segment, "2001:db8::/32")
        self.assertEqual(self.ipset.query("2001:db8::1").priority, 6)
        self.assertFalse(self.ipset.query("2001:db9::1").hit)

    def test_match_and_contains(self):
        self.assertEqual(self.ipset.match("192.168.1.1"), ("192.168.0.0/16", 8))
        self.assertIsNone(self.ipset.match("192.167.1.1"))
        self.assertIn("192.168.1.1", self.ipset)
        self.assertNotIn("1.1.1.1", self.ipset)

    def test_int_address_input(self):
        self.assertTrue(self.ipset.query(0x0A000007).hit)  # 10.0.0.7

    def test_invalid_address_raises(self):
        with self.assertRaises(ValueError):
            self.ipset.query("not-an-ip")
        with self.assertRaises(ValueError):
            IPSet([("999.0.0.0/24", 1)])


class TestLookupCorrectnessAgainstLinearScan(unittest.TestCase):
    """随机数据下，二分索引的结果必须与线性扫描完全一致。"""

    def test_random_cross_check(self):
        import random

        rng = random.Random(20261003)
        entries = []
        for _ in range(3000):
            base = rng.randint(0x0A000000, 0x0AFFFF00)
            entries.append((f"10.{(base >> 16) & 0xFF}.{(base >> 8) & 0xFF}.0/24",
                            rng.randint(1, 100)))
        ipset = IPSet(entries)
        nets = [s.network for s in ipset.segments]
        for _ in range(2000):
            value = rng.randint(0x09000000, 0x0B000000)
            addr = ".".join(str((value >> shift) & 0xFF) for shift in (24, 16, 8, 0))
            expected = None
            for seg in ipset.segments:
                net = seg.network
                if int(net.network_address) <= value <= int(net.broadcast_address):
                    expected = (str(net), seg.priority)
                    break
            self.assertEqual(ipset.match(addr), expected)
            self.assertEqual(len(nets), len(ipset))


if __name__ == "__main__":
    unittest.main()
