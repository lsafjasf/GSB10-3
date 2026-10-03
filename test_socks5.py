#!/usr/bin/env python3
"""Unit tests for the socks5 handshake library (stdlib unittest)."""

from __future__ import annotations

import socket
import threading
import unittest

import socks5
from socks5 import (MalformedReply, Socks5Error, TruncatedReply,
                    describe_reply, encode_address, parse_reply)


def make_reply(rep=0x00, atyp=socks5.ATYP_IPV4, addr=b"\x7f\x00\x00\x01",
               port=0x0050, ver=0x05, rsv=0x00, domain_len=None):
    if atyp == socks5.ATYP_DOMAIN:
        n = len(addr) if domain_len is None else domain_len
        body = bytes([n]) + addr
    else:
        body = addr
    return (bytes([ver, rep, rsv, atyp]) + body
            + port.to_bytes(2, "big"))


class ParseAddressTypesTest(unittest.TestCase):
    def test_ipv4(self):
        r = parse_reply(make_reply(addr=b"\x7f\x00\x00\x01", port=8080))
        self.assertEqual(r.atyp, socks5.ATYP_IPV4)
        self.assertEqual(r.addr, "127.0.0.1")
        self.assertEqual(r.addr_raw, b"\x7f\x00\x00\x01")
        self.assertEqual(r.port, 8080)
        self.assertTrue(r.ok)
        self.assertEqual(r.result, "succeeded")

    def test_ipv6(self):
        raw = bytes(range(16))
        r = parse_reply(make_reply(atyp=socks5.ATYP_IPV6, addr=raw, port=443))
        self.assertEqual(r.atyp, socks5.ATYP_IPV6)
        self.assertEqual(r.addr_raw, raw)
        self.assertEqual(r.addr, socket.inet_ntop(socket.AF_INET6, raw))
        self.assertEqual(r.port, 443)

    def test_domain(self):
        r = parse_reply(make_reply(atyp=socks5.ATYP_DOMAIN,
                                   addr=b"example.com", port=0x1F90))
        self.assertEqual(r.atyp, socks5.ATYP_DOMAIN)
        self.assertEqual(r.addr, "example.com")
        self.assertEqual(r.addr_raw, b"example.com")
        self.assertEqual(r.port, 0x1F90)


class DomainLengthBoundaryTest(unittest.TestCase):
    def test_len_1_minimum(self):
        r = parse_reply(make_reply(atyp=socks5.ATYP_DOMAIN, addr=b"a"))
        self.assertEqual(r.addr, "a")
        self.assertEqual(r.port, 0x0050)

    def test_len_255_maximum(self):
        addr = b"x" * 255
        r = parse_reply(make_reply(atyp=socks5.ATYP_DOMAIN, addr=addr,
                                   port=0xFFFF))
        self.assertEqual(r.addr_raw, addr)
        self.assertEqual(r.port, 0xFFFF)

    def test_len_0_is_malformed(self):
        with self.assertRaises(MalformedReply):
            parse_reply(make_reply(atyp=socks5.ATYP_DOMAIN, addr=b"",
                                   domain_len=0))

    def test_len_byte_is_single_octet_not_16bit(self):
        # LEN=0x01 followed by "a" and port: a parser that (wrongly) reads a
        # 16-bit length would consume 0x0161 bytes and fail/desync.
        r = parse_reply(make_reply(atyp=socks5.ATYP_DOMAIN, addr=b"a",
                                   port=0x1234))
        self.assertEqual(r.port, 0x1234)

    def test_len_overrun_is_truncated_not_misaligned(self):
        # LEN claims 255 but only 10 name bytes + port follow.
        data = make_reply(atyp=socks5.ATYP_DOMAIN, addr=b"a" * 10,
                          domain_len=255)
        with self.assertRaises(TruncatedReply):
            parse_reply(data)


class TruncationTest(unittest.TestCase):
    def test_every_prefix_of_domain_reply(self):
        full = make_reply(atyp=socks5.ATYP_DOMAIN, addr=b"ab.cn")
        for i in range(len(full)):
            with self.assertRaises(TruncatedReply, msg="prefix %d" % i):
                parse_reply(full[:i])

    def test_every_prefix_of_ipv4_reply(self):
        full = make_reply()
        for i in range(len(full)):
            with self.assertRaises(TruncatedReply, msg="prefix %d" % i):
                parse_reply(full[:i])

    def test_every_prefix_of_ipv6_reply(self):
        full = make_reply(atyp=socks5.ATYP_IPV6, addr=bytes(range(16)))
        for i in range(len(full)):
            with self.assertRaises(TruncatedReply, msg="prefix %d" % i):
                parse_reply(full[:i])

    def test_trailing_garbage_rejected(self):
        with self.assertRaises(MalformedReply):
            parse_reply(make_reply() + b"\xAA")


class ReplyCodeTest(unittest.TestCase):
    def test_all_assigned_codes_have_messages(self):
        expected = {
            0x00: "succeeded",
            0x01: "general SOCKS server failure",
            0x02: "connection not allowed by ruleset",
            0x03: "network unreachable",
            0x04: "host unreachable",
            0x05: "connection refused",
            0x06: "TTL expired",
            0x07: "command not supported",
            0x08: "address type not supported",
        }
        for code, message in expected.items():
            self.assertEqual(describe_reply(code), message)

    def test_only_zero_is_success(self):
        for rep in range(0x00, 0x09):
            r = parse_reply(make_reply(rep=rep))
            self.assertEqual(r.ok, rep == 0x00, "rep=0x%02x" % rep)

    def test_unknown_codes_are_not_success(self):
        for rep in (0x09, 0x7F, 0xFF):
            r = parse_reply(make_reply(rep=rep))
            self.assertFalse(r.ok, "rep=0x%02x" % rep)
            self.assertIn("unassigned", r.result)
            self.assertIn("0x%02x" % rep, r.result)


class StructuralErrorTest(unittest.TestCase):
    def test_bad_version(self):
        with self.assertRaises(MalformedReply):
            parse_reply(make_reply(ver=0x04))

    def test_bad_rsv(self):
        with self.assertRaises(MalformedReply):
            parse_reply(make_reply(rsv=0x01))

    def test_unknown_atyp(self):
        with self.assertRaises(MalformedReply):
            parse_reply(make_reply(atyp=0x02))


class EncodeAddressTest(unittest.TestCase):
    def test_ipv4(self):
        self.assertEqual(encode_address("127.0.0.1"),
                         b"\x01\x7f\x00\x00\x01")

    def test_ipv6(self):
        self.assertEqual(encode_address("::1"),
                         b"\x04" + b"\x00" * 15 + b"\x01")

    def test_domain(self):
        self.assertEqual(encode_address("example.com"),
                         b"\x03\x0bexample.com")

    def test_domain_too_long(self):
        with self.assertRaises(ValueError):
            encode_address("a" * 256 + ".com")


def run_server(sock, script):
    """Feed a scripted fake proxy server on one end of a socketpair."""
    def _run():
        try:
            script(sock)
        finally:
            sock.close()
    t = threading.Thread(target=_run)
    t.start()
    return t


def recv_exactly(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise AssertionError("client closed early")
        buf += chunk
    return buf


def standard_server(reply, expect_atyp=None, expect_addr=None,
                    expect_port=None):
    """Server script: negotiate no-auth, check the request, send `reply`."""
    def script(sock):
        greeting = recv_exactly(sock, 2)
        assert greeting[0] == 0x05
        methods = recv_exactly(sock, greeting[1])
        assert 0x00 in methods
        sock.sendall(b"\x05\x00")
        header = recv_exactly(sock, 4)
        assert header[:3] == b"\x05\x01\x00"
        atyp = header[3]
        if expect_atyp is not None:
            assert atyp == expect_atyp, (atyp, expect_atyp)
        if atyp == 0x01:
            addr = recv_exactly(sock, 4)
        elif atyp == 0x04:
            addr = recv_exactly(sock, 16)
        else:
            n = recv_exactly(sock, 1)[0]
            addr = recv_exactly(sock, n)
        port = int.from_bytes(recv_exactly(sock, 2), "big")
        if expect_addr is not None:
            assert addr == expect_addr, (addr, expect_addr)
        if expect_port is not None:
            assert port == expect_port, (port, expect_port)
        sock.sendall(reply)
    return script


class HandshakeTest(unittest.TestCase):
    def connect(self, script, host="example.com", port=80):
        client, server = socket.socketpair()
        t = run_server(server, script)
        try:
            return socks5.socks5_connect(client, host, port)
        finally:
            client.close()
            t.join(timeout=5)

    def test_connect_domain_target_ipv4_bind(self):
        reply = make_reply(addr=b"\x0a\x00\x00\x01", port=31337)
        r = self.connect(standard_server(
            reply, expect_atyp=0x03, expect_addr=b"example.com",
            expect_port=80))
        self.assertTrue(r.ok)
        self.assertEqual((r.addr, r.port), ("10.0.0.1", 31337))

    def test_connect_ipv4_target(self):
        reply = make_reply(addr=b"\x0a\x00\x00\x02", port=1)
        r = self.connect(standard_server(
            reply, expect_atyp=0x01, expect_addr=b"\x7f\x00\x00\x01",
            expect_port=8080), host="127.0.0.1", port=8080)
        self.assertTrue(r.ok)

    def test_connect_ipv6_target_domain_bind(self):
        reply = make_reply(atyp=socks5.ATYP_DOMAIN, addr=b"relay.internal",
                           port=9000)
        r = self.connect(standard_server(
            reply, expect_atyp=0x04,
            expect_addr=b"\x00" * 15 + b"\x01", expect_port=443),
            host="::1", port=443)
        self.assertTrue(r.ok)
        self.assertEqual((r.addr, r.port), ("relay.internal", 9000))

    def test_connect_ipv6_bind(self):
        raw = bytes(range(16))
        reply = make_reply(atyp=socks5.ATYP_IPV6, addr=raw, port=65535)
        r = self.connect(standard_server(reply, expect_atyp=0x03,
                                         expect_addr=b"example.com",
                                         expect_port=80))
        self.assertEqual(r.addr_raw, raw)
        self.assertEqual(r.port, 65535)

    def test_refused_reply_is_not_success(self):
        reply = make_reply(rep=0x05)
        r = self.connect(standard_server(reply, expect_atyp=0x03,
                                         expect_addr=b"example.com",
                                         expect_port=80))
        self.assertFalse(r.ok)
        self.assertEqual(r.result, "connection refused")

    def test_unknown_reply_code_is_not_success(self):
        reply = make_reply(rep=0x42)
        r = self.connect(standard_server(reply, expect_atyp=0x03,
                                         expect_addr=b"example.com",
                                         expect_port=80))
        self.assertFalse(r.ok)
        self.assertIn("unassigned", r.result)

    def test_truncated_reply_from_server(self):
        # Server sends a domain reply whose LEN promises more than arrives.
        def script(sock):
            recv_exactly(sock, 2 + 1)          # greeting w/ 1 method
            sock.sendall(b"\x05\x00")
            recv_exactly(sock, 4 + 1 + 11 + 2)  # request for example.com:80
            sock.sendall(b"\x05\x00\x00\x03\xffshort")
            # close without sending the remaining bytes
        with self.assertRaises(TruncatedReply):
            self.connect(script)

    def test_no_acceptable_methods(self):
        def script(sock):
            recv_exactly(sock, 2 + 1)
            sock.sendall(b"\x05\xff")
        with self.assertRaises(Socks5Error):
            self.connect(script)

    def test_server_picks_unoffered_method(self):
        def script(sock):
            recv_exactly(sock, 2 + 1)
            sock.sendall(b"\x05\x02")  # user/pass was never offered
        with self.assertRaises(MalformedReply):
            self.connect(script)


if __name__ == "__main__":
    unittest.main()
