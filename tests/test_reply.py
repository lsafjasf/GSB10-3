"""解析器与客户端握手的单元测试（标准库 unittest）。"""

import socket
import threading
import unittest

from socks5.client import ReplyError, Socks5Client
from socks5.reply import (
    ATYP_DOMAIN,
    ATYP_IPV4,
    ATYP_IPV6,
    BadVersion,
    TruncatedReply,
    UnknownAddressType,
    UnknownReplyCode,
    parse_reply,
)


def reply_bytes(rep, atyp, addr, port):
    return bytes([0x05, rep, 0x00, atyp]) + addr + port.to_bytes(2, "big")


class ParseAddressTypesTest(unittest.TestCase):
    def test_ipv4(self):
        r = parse_reply(reply_bytes(0, ATYP_IPV4, bytes([10, 0, 0, 1]), 1080))
        self.assertTrue(r.ok)
        self.assertEqual(r.bnd_addr, "10.0.0.1")
        self.assertEqual(r.bnd_port, 1080)
        self.assertEqual(r.rep_message, "succeeded")

    def test_ipv6(self):
        raw = bytes.fromhex("20010db8000000000000000000000001")
        r = parse_reply(reply_bytes(0, ATYP_IPV6, raw, 443))
        self.assertEqual(r.bnd_addr, "2001:db8::1")
        self.assertEqual(r.bnd_port, 443)

    def test_domain(self):
        label = b"example.com"
        r = parse_reply(reply_bytes(0, ATYP_DOMAIN, bytes([len(label)]) + label, 8080))
        self.assertEqual(r.bnd_addr, "example.com")
        self.assertEqual(r.bnd_port, 8080)

    def test_failure_code_maps_to_message(self):
        r = parse_reply(reply_bytes(0x05, ATYP_IPV4, b"\x00" * 4, 0))
        self.assertFalse(r.ok)
        self.assertEqual(r.rep_message, "connection refused")


class DomainLengthBoundaryTest(unittest.TestCase):
    def test_len_1(self):
        r = parse_reply(reply_bytes(0, ATYP_DOMAIN, b"\x01a", 1))
        self.assertEqual(r.bnd_addr, "a")

    def test_len_255_max(self):
        label = b"x" * 255
        r = parse_reply(reply_bytes(0, ATYP_DOMAIN, bytes([255]) + label, 65535))
        self.assertEqual(r.bnd_addr, label.decode())
        self.assertEqual(r.bnd_port, 65535)

    def test_declared_length_overruns_buffer(self):
        # 声明 255 字节但只到了 254：必须报截断，不能错位读端口
        data = bytes([0x05, 0, 0, ATYP_DOMAIN, 255]) + b"x" * 254
        with self.assertRaises(TruncatedReply):
            parse_reply(data)

    def test_missing_length_octet(self):
        with self.assertRaises(TruncatedReply):
            parse_reply(bytes([0x05, 0, 0, ATYP_DOMAIN]))


class TruncationTest(unittest.TestCase):
    def test_every_prefix_of_ipv4_reply(self):
        full = reply_bytes(0, ATYP_IPV4, bytes([1, 2, 3, 4]), 1080)
        for cut in range(len(full)):
            with self.assertRaises(TruncatedReply, msg=f"cut={cut}"):
                parse_reply(full[:cut])

    def test_every_prefix_of_domain_reply(self):
        label = b"example.com"
        full = reply_bytes(0, ATYP_DOMAIN, bytes([len(label)]) + label, 80)
        for cut in range(len(full)):
            with self.assertRaises(TruncatedReply, msg=f"cut={cut}"):
                parse_reply(full[:cut])

    def test_every_prefix_of_ipv6_reply(self):
        full = reply_bytes(0, ATYP_IPV6, bytes(16), 22)
        for cut in range(len(full)):
            with self.assertRaises(TruncatedReply, msg=f"cut={cut}"):
                parse_reply(full[:cut])


class UnknownAndInvalidTest(unittest.TestCase):
    def test_unknown_reply_codes_not_success(self):
        for code in (0x09, 0x10, 0x7F, 0xFF):
            with self.assertRaises(UnknownReplyCode, msg=f"code=0x{code:02x}"):
                parse_reply(reply_bytes(code, ATYP_IPV4, b"\x00" * 4, 0))

    def test_all_known_codes_parse(self):
        for code in range(0x00, 0x09):
            r = parse_reply(reply_bytes(code, ATYP_IPV4, b"\x00" * 4, 0))
            self.assertEqual(r.rep, code)
            self.assertTrue(r.rep_message)

    def test_unknown_atyp(self):
        for atyp in (0x00, 0x02, 0x05, 0xFF):
            with self.assertRaises(UnknownAddressType, msg=f"atyp=0x{atyp:02x}"):
                parse_reply(reply_bytes(0, atyp, b"\x00" * 4, 0))

    def test_bad_version(self):
        with self.assertRaises(BadVersion):
            parse_reply(bytes([0x04, 0, 0, 1, 1, 2, 3, 4, 0, 80]))


def run_fake_proxy(server_sock, reply):
    """模拟代理：收协商 -> 回 05 00，收请求 -> 回指定应答。"""
    def serve():
        conn, _ = server_sock.accept()
        conn.recv(64)
        conn.sendall(b"\x05\x00")
        conn.recv(512)
        conn.sendall(reply)
        conn.close()
    threading.Thread(target=serve, daemon=True).start()


class ClientHandshakeTest(unittest.TestCase):
    def _roundtrip(self, host, port, reply):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        run_fake_proxy(server, reply)
        sock = socket.create_connection(server.getsockname())
        try:
            return Socks5Client(sock).open(host, port)
        finally:
            sock.close()
            server.close()

    def test_connect_ipv4(self):
        reply = reply_bytes(0, ATYP_IPV4, bytes([127, 0, 0, 1]), 9000)
        r = self._roundtrip("93.184.216.34", 443, reply)
        self.assertEqual((r.bnd_addr, r.bnd_port), ("127.0.0.1", 9000))

    def test_connect_domain(self):
        label = b"example.com"
        reply = reply_bytes(0, ATYP_DOMAIN, bytes([len(label)]) + label, 8080)
        r = self._roundtrip("example.com", 80, reply)
        self.assertEqual(r.bnd_addr, "example.com")

    def test_connect_ipv6(self):
        reply = reply_bytes(0, ATYP_IPV6, bytes(15) + b"\x01", 443)
        r = self._roundtrip("::1", 443, reply)
        self.assertEqual(r.bnd_addr, "::1")

    def test_refused_reply_raises(self):
        reply = reply_bytes(0x05, ATYP_IPV4, b"\x00" * 4, 0)
        with self.assertRaises(ReplyError) as ctx:
            self._roundtrip("example.com", 80, reply)
        self.assertIn("connection refused", str(ctx.exception))

    def test_truncated_reply_raises(self):
        reply = reply_bytes(0, ATYP_IPV4, bytes([1, 2, 3, 4]), 80)[:6]
        with self.assertRaises(Exception):
            self._roundtrip("example.com", 80, reply)


if __name__ == "__main__":
    unittest.main()
