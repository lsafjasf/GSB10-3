"""Self-tests for the forwarded-header trust logic.

Run:  python3 -m unittest test_forwarded -v   (from the src/ directory)
"""

import ipaddress
import unittest

from forwarded import (
    TrustedProxies,
    parse_chain,
    resolve_client,
    EMPTY,
    INVALID,
    PORT,
    UNKNOWN,
)

# Two layers of trusted proxies plus a management CIDR.
TRUST = TrustedProxies([
    "10.0.0.0/8",       # internal proxy network
    "192.168.10.1/32",  # edge proxy
    "2001:db8::/48",    # internal IPv6 proxies
])


class ParseChainTests(unittest.TestCase):
    def test_valid_ipv4_chain(self):
        report = parse_chain("203.0.113.1, 10.0.0.2, 10.0.0.3")
        self.assertTrue(report.ok)
        self.assertEqual(
            [str(e.ip) for e in report.entries],
            ["203.0.113.1", "10.0.0.2", "10.0.0.3"],
        )

    def test_valid_ipv6_chain(self):
        report = parse_chain("2001:db8::2, 2001:db8::1")
        self.assertTrue(report.ok)
        self.assertEqual(report.entries[0].ip.version, 6)

    def test_empty_and_missing_header(self):
        self.assertEqual(parse_chain(None).entries, [])
        report = parse_chain("")
        self.assertEqual(len(report.entries), 1)
        self.assertEqual(report.errors[0].kind, EMPTY)
        self.assertEqual(report.errors[0].index, 0)

    def test_errors_are_positioned(self):
        report = parse_chain("203.0.113.1, , not-an-ip, 10.0.0.2")
        self.assertFalse(report.ok)
        self.assertEqual(
            [(e.index, e.kind) for e in report.errors],
            [(1, EMPTY), (2, INVALID)],
        )

    def test_unknown_marker_is_locatable(self):
        report = parse_chain("unknown, 10.0.0.1")
        self.assertEqual(report.errors[0].kind, UNKNOWN)
        self.assertEqual(report.errors[0].index, 0)

    def test_port_tokens_are_refused(self):
        for bad in ("192.0.2.1:8080", "[2001:db8::1]:443"):
            report = parse_chain(bad)
            self.assertEqual(report.errors[0].kind, PORT, bad)
            self.assertEqual(report.errors[0].raw, bad)

    def test_bare_ipv6_without_brackets_is_valid(self):
        report = parse_chain("2001:db8::1")
        self.assertTrue(report.ok)

    def test_whitespace_is_trimmed(self):
        report = parse_chain("  203.0.113.1  ,  10.0.0.1 ")
        self.assertTrue(report.ok)


class UntrustedPeerTests(unittest.TestCase):
    """Header from a direct, untrusted connection must be ignored."""

    def test_plain_direct_connection(self):
        r = resolve_client("198.51.100.7", None, TRUST)
        self.assertEqual(r.client_ip, "198.51.100.7")
        self.assertEqual(r.source, "direct")
        self.assertFalse(r.header_used)

    def test_spoofed_header_from_untrusted_peer(self):
        r = resolve_client(
            "198.51.100.7", "203.0.113.9, 10.0.0.1", TRUST
        )
        self.assertEqual(r.client_ip, "198.51.100.7")
        self.assertFalse(r.header_used)
        self.assertIn("ignored", r.reason)


class SingleProxyTests(unittest.TestCase):
    def test_single_trusted_proxy(self):
        # edge proxy 192.168.10.1 saw client 203.0.113.50
        r = resolve_client("192.168.10.1", "203.0.113.50", TRUST)
        self.assertEqual(r.client_ip, "203.0.113.50")
        self.assertEqual(r.source, "forwarded")
        self.assertEqual(r.trusted_hops, 1)

    def test_client_prepends_spoofed_value_single_proxy(self):
        # Client sent "1.2.3.4"; the trusted proxy appended the real peer.
        r = resolve_client(
            "192.168.10.1", "1.2.3.4, 203.0.113.50", TRUST
        )
        self.assertEqual(r.client_ip, "203.0.113.50")
        self.assertTrue(r.header_used)
        self.assertIn("ignored", r.reason)

    def test_trusted_proxy_without_header(self):
        r = resolve_client("192.168.10.1", None, TRUST)
        self.assertEqual(r.client_ip, "192.168.10.1")
        self.assertFalse(r.header_used)


class MultiProxyTests(unittest.TestCase):
    def test_two_trusted_hops(self):
        # client -> edge 192.168.10.1 -> inner 10.0.0.2 -> us
        r = resolve_client(
            "10.0.0.2", "203.0.113.50, 192.168.10.1", TRUST
        )
        self.assertEqual(r.client_ip, "203.0.113.50")
        self.assertEqual(r.trusted_hops, 2)

    def test_three_hops_with_ipv6(self):
        r = resolve_client(
            "2001:db8::2",
            "203.0.113.50, 2001:db8::dead:beef, 2001:db8::1",
            TRUST,
        )
        # 2001:db8::dead:beef is inside the trusted /48, so it is skipped;
        # the first untrusted address is the real client.
        self.assertEqual(r.client_ip, "203.0.113.50")
        self.assertEqual(r.trusted_hops, 3)

    def test_all_entries_trusted_uses_leftmost(self):
        r = resolve_client(
            "10.0.0.3", "10.0.0.5, 10.0.0.4", TRUST
        )
        self.assertEqual(r.client_ip, "10.0.0.5")
        self.assertIn("leftmost", r.reason)

    def test_forged_prefix_across_multiple_proxies(self):
        # Attacker prepends two fake admin addresses; proxies append truth.
        r = resolve_client(
            "10.0.0.2",
            "10.0.0.99, 169.254.0.1, 203.0.113.50, 192.168.10.1",
            TRUST,
        )
        self.assertEqual(r.client_ip, "203.0.113.50")


class MalformedChainTests(unittest.TestCase):
    def test_invalid_item_before_real_client(self):
        # rightmost entries are trusted; the walk must stop at item #2 and
        # fall back to the peer because the real client cannot be recovered.
        r = resolve_client(
            "10.0.0.2",
            "203.0.113.50, garbage, 192.168.10.1",
            TRUST,
        )
        self.assertEqual(r.client_ip, "10.0.0.2")
        self.assertFalse(r.header_used)
        self.assertIn("#1", r.reason)
        self.assertIn("invalid", r.reason)

    def test_invalid_item_after_untrusted_client_is_irrelevant(self):
        # Bad item sits to the LEFT of the client: never reached by the walk.
        r = resolve_client(
            "10.0.0.2",
            "garbage, 203.0.113.50, 192.168.10.1",
            TRUST,
        )
        self.assertEqual(r.client_ip, "203.0.113.50")
        self.assertTrue(r.header_used)

    def test_empty_item_in_chain(self):
        r = resolve_client(
            "10.0.0.2", "203.0.113.50, , 192.168.10.1", TRUST
        )
        self.assertEqual(r.client_ip, "10.0.0.2")
        self.assertIn("#1", r.reason)
        self.assertIn("empty", r.reason)

    def test_unknown_marker_truncates_chain(self):
        r = resolve_client(
            "10.0.0.2", "unknown, 203.0.113.50, 192.168.10.1", TRUST
        )
        # "unknown" is item #0, left of the client, so it is never reached.
        self.assertEqual(r.client_ip, "203.0.113.50")

        # But when "unknown" sits where the client must be, we fall back.
        r2 = resolve_client(
            "10.0.0.2", "unknown, 192.168.10.1", TRUST
        )
        self.assertEqual(r2.client_ip, "10.0.0.2")
        self.assertIn("unknown", r2.reason)

    def test_port_token_truncates_chain(self):
        r = resolve_client(
            "10.0.0.2", "203.0.113.50:443, 192.168.10.1", TRUST
        )
        self.assertEqual(r.client_ip, "10.0.0.2")
        self.assertIn("port", r.reason)

    def test_header_with_only_bad_items(self):
        r = resolve_client("10.0.0.2", "a, b, c", TRUST)
        self.assertEqual(r.client_ip, "10.0.0.2")
        self.assertFalse(r.header_used)

    def test_error_locations_are_exact(self):
        r = resolve_client(
            "10.0.0.2", "1.1.1.1, x, 2.2.2.2, y, 192.168.10.1", TRUST
        )
        # Walk: #4 trusted, #3 invalid -> stop. Location #3 reported.
        self.assertIn("#3", r.reason)
        self.assertEqual(
            [e.index for e in r.report.errors if e.kind == INVALID],
            [1, 3],
        )


class MiscTests(unittest.TestCase):
    def test_cidr_based_trust(self):
        r = resolve_client(
            "10.99.99.99", "203.0.113.50, 10.1.1.1", TRUST
        )
        self.assertEqual(r.client_ip, "203.0.113.50")

    def test_ip_object_returned_by_parser(self):
        report = parse_chain("203.0.113.50")
        self.assertIsInstance(report.entries[0].ip,
                              ipaddress.IPv4Address)

    def test_summary_text(self):
        r = resolve_client("192.168.10.1", "203.0.113.50", TRUST)
        self.assertIn("X-Forwarded-For", r.summary())


if __name__ == "__main__":
    unittest.main(verbosity=2)
