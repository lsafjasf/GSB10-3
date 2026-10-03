"""ipset_lib 自测：覆盖单段、相邻、完全包含、完全相同、跨版本混合、边界地址等。

运行：python3 -m unittest test_ipset_lib -v
"""

import ipaddress
import unittest

from ipset_lib import AddressSet, LookupResult, merge_networks


class TestMerge(unittest.TestCase):
    def test_single_segment_unchanged(self):
        """单段输入原样返回。"""
        self.assertEqual(merge_networks(["10.0.0.0/24"]),
                         [ipaddress.ip_network("10.0.0.0/24")])

    def test_adjacent_ipv4_merge(self):
        """相邻两个 /24 合并为 /23。"""
        merged = merge_networks(["10.0.0.0/24", "10.0.1.0/24"])
        self.assertEqual(merged, [ipaddress.ip_network("10.0.0.0/23")])

    def test_adjacent_chain_to_default(self):
        """0.0.0.0/1 与 128.0.0.0/1 相邻，合并为 0.0.0.0/0。"""
        merged = merge_networks(["0.0.0.0/1", "128.0.0.0/1"])
        self.assertEqual(merged, [ipaddress.ip_network("0.0.0.0/0")])

    def test_non_adjacent_not_merged(self):
        """不相邻的两个段保持独立。"""
        merged = merge_networks(["10.0.0.0/24", "10.0.2.0/24"])
        self.assertEqual(len(merged), 2)

    def test_containment_collapsed(self):
        """大段包含小段时只保留大段。"""
        merged = merge_networks(["10.0.0.0/8", "10.1.0.0/16",
                                 "10.1.2.0/24"])
        self.assertEqual(merged, [ipaddress.ip_network("10.0.0.0/8")])

    def test_identical_dedup(self):
        """完全相同的段去重。"""
        merged = merge_networks(["192.168.1.0/24", "192.168.1.0/24"])
        self.assertEqual(merged, [ipaddress.ip_network("192.168.1.0/24")])

    def test_partial_overlap_merge(self):
        """部分重叠（10.0.0.0/23 与 10.0.1.0/24）也会合并。"""
        merged = merge_networks(["10.0.0.0/23", "10.0.1.0/24"])
        self.assertEqual(merged, [ipaddress.ip_network("10.0.0.0/23")])

    def test_host_routes_adjacent(self):
        """/32 单主机地址相邻合并。"""
        merged = merge_networks(["192.0.2.0/32", "192.0.2.1/32"])
        self.assertEqual(merged, [ipaddress.ip_network("192.0.2.0/31")])

    def test_ipv6_adjacent_merge(self):
        """IPv6 相邻段合并：两个 /33 -> /32。"""
        merged = merge_networks(["2001:db8::/33", "2001:db8:8000::/33"])
        self.assertEqual(merged, [ipaddress.ip_network("2001:db8::/32")])

    def test_ipv6_containment_and_host(self):
        """IPv6 包含关系与 /128 单主机段。"""
        merged = merge_networks(["2001:db8::/32", "2001:db8::1/128"])
        self.assertEqual(merged, [ipaddress.ip_network("2001:db8::/32")])

    def test_mixed_versions_never_merged(self):
        """IPv4 / IPv6 混合输入：分别合并，绝不互相混算。"""
        merged = merge_networks([
            "10.0.0.0/24", "10.0.1.0/24",          # -> 10.0.0.0/23
            "2001:db8::/33", "2001:db8:8000::/33",  # -> 2001:db8::/32
        ])
        self.assertEqual(merged, [
            ipaddress.ip_network("10.0.0.0/23"),
            ipaddress.ip_network("2001:db8::/32"),
        ])

    def test_ipv4_mapped_ipv6_stays_v6(self):
        """IPv4-mapped IPv6 仍属 IPv6，不能与同名 v4 段合并。"""
        merged = merge_networks(["10.0.0.0/24", "::ffff:10.0.0.0/120"])
        self.assertEqual(len(merged), 2)
        self.assertEqual({n.version for n in merged}, {4, 6})

    def test_loose_host_bits_normalized(self):
        """带主机位的输入自动归一到网段。"""
        merged = merge_networks(["10.0.0.5/24"])
        self.assertEqual(merged, [ipaddress.ip_network("10.0.0.0/24")])


class TestLookup(unittest.TestCase):
    def setUp(self):
        self.aset = AddressSet([
            ("10.0.0.0/24", 10),
            ("10.0.1.0/24", 20),       # 与上段相邻，合并为 10.0.0.0/23
            ("192.168.0.0/16", None),  # 默认优先级 = 前缀长度 16
            ("2001:db8::/32", 5),
        ])

    def test_hit_returns_segment_and_priority(self):
        """命中：返回合并后的段和被吸收段中的最大优先级。"""
        r = self.aset.lookup("10.0.1.7")
        self.assertTrue(r.hit)
        self.assertEqual(str(r.network), "10.0.0.0/23")
        self.assertEqual(r.priority, 20)

    def test_default_priority_is_prefixlen(self):
        """未显式指定优先级时，默认取前缀长度。"""
        r = self.aset.lookup("192.168.5.5")
        self.assertTrue(r.hit)
        self.assertEqual(str(r.network), "192.168.0.0/16")
        self.assertEqual(r.priority, 16)

    def test_miss_is_explicit(self):
        """未命中给出明确结果 hit=False。"""
        r = self.aset.lookup("8.8.8.8")
        self.assertIsInstance(r, LookupResult)
        self.assertFalse(r.hit)
        self.assertIsNone(r.network)
        self.assertIsNone(r.priority)

    def test_boundary_first_and_last_address(self):
        """段边界：首地址、末地址都算命中。"""
        for ip in ("10.0.0.0", "10.0.1.255"):
            self.assertTrue(self.aset.lookup(ip).hit, ip)
        # 紧邻边界之外未命中
        self.assertFalse(self.aset.lookup("10.0.2.0").hit)
        self.assertFalse(self.aset.lookup("9.255.255.255").hit)

    def test_ipv6_hit_and_miss(self):
        self.assertTrue(self.aset.lookup("2001:db8::1").hit)
        self.assertTrue(self.aset.lookup("2001:db8:ffff::1").hit)
        miss = self.aset.lookup("2001:db9::1")
        self.assertFalse(miss.hit)
        self.assertEqual(miss.version, 6)

    def test_gap_between_segments_is_miss(self):
        aset = AddressSet(["172.16.0.0/24", "172.16.2.0/24"])
        self.assertTrue(aset.lookup("172.16.0.1").hit)
        self.assertFalse(aset.lookup("172.16.1.1").hit)
        self.assertTrue(aset.lookup("172.16.2.255").hit)

    def test_empty_set_lookup(self):
        aset = AddressSet()
        self.assertFalse(aset.lookup("1.2.3.4").hit)
        self.assertEqual(aset.merged(), [])

    def test_default_route_matches_all(self):
        aset = AddressSet(["0.0.0.0/0", "::/0"])
        self.assertEqual(str(aset.lookup("1.2.3.4").network), "0.0.0.0/0")
        self.assertEqual(str(aset.lookup("fe80::1").network), "::/0")

    def test_merged_view_grouped_by_version(self):
        v4 = [str(n) for n in self.aset.merged(4)]
        v6 = [str(n) for n in self.aset.merged(6)]
        self.assertEqual(v4, ["10.0.0.0/23", "192.168.0.0/16"])
        self.assertEqual(v6, ["2001:db8::/32"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
