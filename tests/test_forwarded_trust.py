import ipaddress
import unittest

from forwarded_trust import (
    parse_forwarded,
    parse_x_forwarded_for,
    resolve_client,
)

TRUSTED = ["10.0.0.0/8", "127.0.0.0/8", "fd00::/8"]


class ParseXForwardedForTests(unittest.TestCase):
    def test_single_entry(self):
        r = parse_x_forwarded_for("203.0.113.7")
        self.assertEqual(len(r.entries), 1)
        self.assertEqual(str(r.entries[0].ip), "203.0.113.7")
        self.assertFalse(r.errors)

    def test_multi_entry_with_whitespace(self):
        r = parse_x_forwarded_for(" 203.0.113.7 , 10.0.0.1 ,10.0.0.2 ")
        self.assertEqual([str(e.ip) for e in r.entries],
                         ["203.0.113.7", "10.0.0.1", "10.0.0.2"])

    def test_ipv6_and_port(self):
        r = parse_x_forwarded_for("2001:db8::1, [2001:db8::2]:443, 192.0.2.1:8080")
        self.assertEqual([str(e.ip) for e in r.entries],
                         ["2001:db8::1", "2001:db8::2", "192.0.2.1"])

    def test_invalid_entry_reports_position(self):
        r = parse_x_forwarded_for("203.0.113.7, not-an-ip, 10.0.0.1")
        self.assertEqual(len(r.errors), 1)
        self.assertEqual(r.errors[0].index, 1)
        self.assertIn("not-an-ip", r.errors[0].error)

    def test_empty_header(self):
        r = parse_x_forwarded_for("")
        self.assertEqual(r.entries, ())

    def test_multiple_invalid_entries(self):
        r = parse_x_forwarded_for("bad, 999.1.1.1, 10.0.0.1")
        self.assertEqual([e.index for e in r.errors], [0, 1])


class ParseForwardedTests(unittest.TestCase):
    def test_basic(self):
        r = parse_forwarded('for=203.0.113.7;proto=https, for=10.0.0.1')
        self.assertEqual([str(e.ip) for e in r.entries],
                         ["203.0.113.7", "10.0.0.1"])

    def test_quoted_ipv6_with_port(self):
        r = parse_forwarded('for="[2001:db8:cafe::17]:4711"')
        self.assertEqual(str(r.entries[0].ip), "2001:db8:cafe::17")

    def test_unknown_identifier_is_error_with_position(self):
        r = parse_forwarded("for=unknown, for=10.0.0.1")
        self.assertEqual(r.errors[0].index, 0)
        self.assertIn("unknown", r.errors[0].error)

    def test_missing_for_param(self):
        r = parse_forwarded("proto=https;host=example.com")
        self.assertEqual(r.errors[0].index, 0)
        self.assertIn("for=", r.errors[0].error)

    def test_obfuscated_identifier(self):
        r = parse_forwarded("for=_hidden")
        self.assertIsNotNone(r.entries[0].error)


class ResolveUntrustedDirectTests(unittest.TestCase):
    """直连对端不可信：头部整体忽略，回退直连地址。"""

    def test_forged_header_ignored(self):
        r = resolve_client("198.51.100.9", TRUSTED,
                           x_forwarded_for="1.1.1.1, 2.2.2.2")
        self.assertEqual(str(r.client_ip), "198.51.100.9")
        self.assertEqual(r.source, "direct")
        self.assertFalse(r.trusted)
        self.assertFalse(r.spoofable)

    def test_forged_forwarded_ignored(self):
        r = resolve_client("198.51.100.9", TRUSTED,
                           forwarded="for=1.1.1.1")
        self.assertEqual(str(r.client_ip), "198.51.100.9")
        self.assertEqual(r.source, "direct")


class ResolveSingleProxyTests(unittest.TestCase):
    def test_single_proxy_single_client(self):
        r = resolve_client("10.0.0.2", TRUSTED, x_forwarded_for="203.0.113.7")
        self.assertEqual(str(r.client_ip), "203.0.113.7")
        self.assertEqual(r.source, "header")
        self.assertEqual(r.hops, ())
        self.assertFalse(r.spoofable)

    def test_trusted_direct_without_header(self):
        r = resolve_client("10.0.0.2", TRUSTED)
        self.assertEqual(str(r.client_ip), "10.0.0.2")
        self.assertEqual(r.source, "direct")
        self.assertTrue(r.trusted)


class ResolveMultiProxyTests(unittest.TestCase):
    def test_two_hops(self):
        # 客户端 -> 10.0.0.1 -> 10.0.0.2 -> 本机
        r = resolve_client("10.0.0.2", TRUSTED,
                           x_forwarded_for="203.0.113.7, 10.0.0.1")
        self.assertEqual(str(r.client_ip), "203.0.113.7")
        self.assertEqual([str(h) for h in r.hops], ["10.0.0.1"])

    def test_three_hops_ipv6(self):
        r = resolve_client("fd00::3", TRUSTED,
                           x_forwarded_for="2001:db8::9, fd00::1, fd00::2")
        self.assertEqual(str(r.client_ip), "2001:db8::9")
        self.assertEqual([str(h) for h in r.hops], ["fd00::1", "fd00::2"])

    def test_client_spoofed_prefix_stripped(self):
        # 客户端伪造了链前缀 "1.1.1.1, 2.2.2.2"，可信代理只追加了真实地址。
        r = resolve_client("10.0.0.2", TRUSTED,
                           x_forwarded_for="1.1.1.1, 2.2.2.2, 203.0.113.7, 10.0.0.1")
        self.assertEqual(str(r.client_ip), "203.0.113.7")
        self.assertFalse(r.spoofable)


class ResolveMalformedChainTests(unittest.TestCase):
    def test_invalid_item_blocks_left_side(self):
        # 位置 1 非法：其左侧（位置 0）不可采信，取右侧首个不可信地址。
        r = resolve_client("10.0.0.2", TRUSTED,
                           x_forwarded_for="1.1.1.1, garbage, 203.0.113.7, 10.0.0.1")
        self.assertEqual(str(r.client_ip), "203.0.113.7")
        self.assertEqual(r.source, "header")
        self.assertEqual(r.blocked_index, None)  # 未触及非法项即已命中

    def test_invalid_item_at_scan_boundary(self):
        # 扫描在位置 1 被截断，右侧全是可信代理 -> 采用最近的合法前缀地址。
        r = resolve_client("10.0.0.2", TRUSTED,
                           x_forwarded_for="203.0.113.7, garbage, 10.0.0.1")
        self.assertEqual(r.blocked_index, 1)
        self.assertEqual(str(r.client_ip), "203.0.113.7")
        self.assertEqual(r.source, "header-prefix")
        self.assertTrue(r.spoofable)

    def test_invalid_item_with_no_valid_entries_falls_back(self):
        r = resolve_client("10.0.0.2", TRUSTED, x_forwarded_for="garbage")
        self.assertEqual(str(r.client_ip), "10.0.0.2")
        self.assertEqual(r.source, "direct")
        self.assertEqual(r.blocked_index, 0)

    def test_unknown_in_forwarded(self):
        r = resolve_client("10.0.0.2", TRUSTED,
                           forwarded="for=unknown, for=203.0.113.7")
        self.assertEqual(str(r.client_ip), "203.0.113.7")
        self.assertEqual(r.source, "header")

    def test_error_position_located(self):
        chain = parse_x_forwarded_for("1.2.3.4, bad-token, 10.0.0.1")
        err = chain.errors[0]
        self.assertEqual((err.index, err.raw), (1, "bad-token"))


class ResolveAllTrustedChainTests(unittest.TestCase):
    def test_all_trusted_uses_leftmost_and_flags_spoofable(self):
        r = resolve_client("10.0.0.2", TRUSTED,
                           x_forwarded_for="10.0.0.9, 10.0.0.1")
        self.assertEqual(str(r.client_ip), "10.0.0.9")
        self.assertEqual(r.source, "header-prefix")
        self.assertTrue(r.spoofable)

    def test_empty_header_falls_back(self):
        r = resolve_client("10.0.0.2", TRUSTED, x_forwarded_for="")
        self.assertEqual(str(r.client_ip), "10.0.0.2")
        self.assertEqual(r.source, "direct")


class ResolveHeaderPrecedenceTests(unittest.TestCase):
    def test_xff_wins_over_forwarded(self):
        r = resolve_client("10.0.0.2", TRUSTED,
                           x_forwarded_for="203.0.113.7",
                           forwarded="for=198.51.100.3")
        self.assertEqual(str(r.client_ip), "203.0.113.7")


class TrustTableTests(unittest.TestCase):
    """README 判定表的逐行验证。"""

    def test_table(self):
        cases = [
            # (direct, xff, expected_ip, expected_source)
            ("198.51.100.9", "1.1.1.1", "198.51.100.9", "direct"),
            ("10.0.0.2", None, "10.0.0.2", "direct"),
            ("10.0.0.2", "203.0.113.7", "203.0.113.7", "header"),
            ("10.0.0.2", "203.0.113.7, 10.0.0.1", "203.0.113.7", "header"),
            ("10.0.0.2", "10.0.0.9, 10.0.0.1", "10.0.0.9", "header-prefix"),
            ("10.0.0.2", "garbage", "10.0.0.2", "direct"),
        ]
        for direct, xff, ip, source in cases:
            with self.subTest(direct=direct, xff=xff):
                r = resolve_client(direct, TRUSTED, x_forwarded_for=xff)
                self.assertEqual(str(r.client_ip), ip)
                self.assertEqual(r.source, source)


if __name__ == "__main__":
    unittest.main()
