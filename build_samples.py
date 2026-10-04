#!/usr/bin/env python3
"""构造 AXFR 响应流样本报文（仅标准库），输出到 samples/ 目录。

样本：
  single.bin         单条记录（一个问题 + 一条 A 记录）
  stream.bin         多报文 AXFR：SOA ... SOA
  compressed.bin     大量压缩指针（名称与 RDATA 内均压缩）
  deep_chain.bin     多级「指针指向指针」深链
  loop.bin           压缩指针成环
  truncated_rr.bin   RR 中途截断（rdlength 与实际内容不符）
  truncated_len.bin  TCP 长度前缀声明大于剩余内容
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dns_axfr as dns  # noqa: E402  # 保留给交互调试使用

SAMPLE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")

IN = 1


def enc_name(name: str) -> bytes:
    """完整编码域名（无压缩）。"""
    if name in (".", ""):
        return b"\x00"
    out = bytearray()
    for label in name.rstrip(".").split("."):
        raw = label.encode("ascii")
        assert len(raw) <= 63
        out.append(len(raw))
        out.extend(raw)
    out.append(0)
    return bytes(out)


def ptr(target: int) -> bytes:
    """2 字节压缩指针。"""
    assert 0 <= target < 0x4000
    return struct.pack("!H", 0xC000 | target)


def label_ptr(label: str, target: int) -> bytes:
    """一个标签 + 指向 target 的压缩指针。"""
    raw = label.encode("ascii")
    return bytes([len(raw)]) + raw + ptr(target)


class MessageBuilder:
    """顺序构造 DNS 报文；put_* 返回写入偏移以便后续压缩引用。"""

    def __init__(self, msg_id=0x1234, aa=True):
        self.buf = bytearray(12)
        self.msg_id = msg_id
        self.flags = 0x8000 | (0x0400 if aa else 0)  # QR=1
        self.qd = self.an = self.ns = self.ar = 0

    def put_name(self, name: str) -> int:
        offset = len(self.buf)
        self.buf.extend(enc_name(name))
        return offset

    def put_label_ptr(self, label: str, target: int) -> int:
        offset = len(self.buf)
        self.buf.extend(label_ptr(label, target))
        return offset

    def add_question(self, name: str, qtype: int, qclass: int = IN):
        self.buf.extend(enc_name(name))
        self.buf.extend(struct.pack("!HH", qtype, qclass))
        self.qd += 1

    def add_rr(self, name_field: bytes, rtype: int, rdata: bytes,
               ttl=3600, rclass: int = IN, section="answer"):
        self.buf.extend(name_field)
        self.buf.extend(struct.pack("!HHIH", rtype, rclass, ttl, len(rdata)))
        self.buf.extend(rdata)
        if section == "answer":
            self.an += 1
        elif section == "authority":
            self.ns += 1
        else:
            self.ar += 1

    def build(self) -> bytes:
        struct.pack_into("!HHHHHH", self.buf, 0, self.msg_id, self.flags,
                         self.qd, self.an, self.ns, self.ar)
        return bytes(self.buf)


def tcp(msg: bytes) -> bytes:
    return struct.pack("!H", len(msg)) + msg


# ---------------------------------------------------------------- 各样本

def build_single() -> bytes:
    b = MessageBuilder(msg_id=0x0001)
    b.add_question("example.com", 252)  # AXFR
    b.add_rr(enc_name("example.com"), 1, bytes([192, 0, 2, 1]), ttl=300)
    return tcp(b.build())


def build_compressed() -> bytes:
    """大量压缩指针：所有记录共享 example.com 后缀，RDATA 内域名也压缩。

    首条记录携带完整名 example.com 作为压缩根，后续名称全部指针化。
    """
    b = MessageBuilder(msg_id=0x0002)
    root = len(b.buf)
    b.add_rr(enc_name("example.com"), 1, bytes([1, 2, 3, 4]))
    www = len(b.buf)
    b.add_rr(label_ptr("www", root), 1, bytes([93, 184, 216, 34]))
    ns1 = len(b.buf)
    b.add_rr(label_ptr("ns1", root), 1, bytes([10, 0, 0, 1]))
    b.add_rr(label_ptr("ns2", root), 1, bytes([10, 0, 0, 2]))
    # MX：偏好值 + 压缩的 exchange 名
    b.add_rr(ptr(root), 15, struct.pack("!H", 10) + ptr(root))
    # CNAME：mail -> www（RDATA 内指针指向另一个压缩名）
    b.add_rr(label_ptr("mail", root), 5, ptr(www))
    # NS：RDATA 内为 ns1 的压缩指针
    b.add_rr(ptr(root), 2, ptr(ns1))
    # 同一名称第二条 A 记录，验证顺序保持
    b.add_rr(ptr(root), 1, bytes([5, 6, 7, 8]))
    return tcp(b.build())


def build_deep_chain() -> bytes:
    """多级「指针指向指针」深链。

    每条记录名 = 一个标签 + 指向上一条记录名的指针，最后一条记录
    直接以指针引用链中间节点，解析需连续跟随 4 跳指针。
    """
    b = MessageBuilder(msg_id=0x0003)
    root = len(b.buf)
    b.add_rr(enc_name("example.com"), 1, bytes([9, 9, 9, 9]))
    c_off = len(b.buf)
    b.add_rr(label_ptr("c", root), 16, bytes([3]) + b"abc")
    b_off = len(b.buf)
    b.add_rr(label_ptr("b", c_off), 1, bytes([8, 8, 8, 8]))
    a_off = len(b.buf)
    b.add_rr(label_ptr("a", b_off), 1, bytes([7, 7, 7, 7]))
    # 指针指向指针链中段：a.b.c.example.com / c.example.com
    b.add_rr(ptr(a_off), 1, bytes([6, 6, 6, 6]))
    b.add_rr(ptr(c_off), 1, bytes([5, 5, 5, 5]))
    return tcp(b.build())


def build_stream() -> bytes:
    """标准 AXFR：报文1 = SOA + 记录，报文2 = 收尾 SOA。"""
    b1 = MessageBuilder(msg_id=0x0010)
    root = len(b1.buf)                       # 问题段域名作为压缩根
    b1.add_question("zone.example", 252)
    soa_rdata = (enc_name("ns.zone.example")
                 + enc_name("hostmaster.zone.example")
                 + struct.pack("!IIIII", 2026010101, 7200, 3600, 1209600, 3600))
    b1.add_rr(ptr(root), 6, soa_rdata)
    b1.add_rr(label_ptr("host", root), 1, bytes([192, 168, 1, 10]))
    b1.add_rr(label_ptr("host", root), 1, bytes([192, 168, 1, 11]))

    b2 = MessageBuilder(msg_id=0x0011)
    b2.add_rr(enc_name("zone.example"), 6, soa_rdata)
    return tcp(b1.build()) + tcp(b2.build())


def build_loop() -> bytes:
    """记录名压缩指针成环：x -> y，y -> x。"""
    b = MessageBuilder(msg_id=0x0004)
    x_off = len(b.buf)                  # x 名称起始
    y_off = x_off + 4                   # x = [1,'x',ptr(2B)] 占 4 字节
    b.buf.extend(label_ptr("x", y_off))
    b.buf.extend(label_ptr("y", x_off))  # y 指回 x，成环
    b.buf.extend(struct.pack("!HHIH", 1, IN, 60, 4))
    b.buf.extend(bytes([1, 1, 1, 1]))
    b.an = 1
    return tcp(b.build())


def build_truncated_rr() -> bytes:
    """第二条 RR 的 rdlength=100，但 RDATA 只有 3 字节（缺 97）。"""
    b = MessageBuilder(msg_id=0x0005)
    root = len(b.buf)
    b.add_rr(enc_name("example.com"), 1, bytes([1, 2, 3, 4]))
    b.add_rr(label_ptr("www", root), 1, bytes([4, 3, 2, 1]))
    # 第三条 RR：rdlength=100，但 RDATA 只有 3 字节（缺 97）
    b.buf.extend(ptr(root))
    b.buf.extend(struct.pack("!HHIH", 1, IN, 60, 100))
    b.buf.extend(b"\x0a\x0b\x0c")       # 仅 3 字节
    b.an = 3
    return tcp(b.build())


def build_truncated_len() -> bytes:
    """TCP 长度前缀声明 80 字节，实际只有 50 字节（缺 30）。"""
    return struct.pack("!H", 80) + bytes(range(50))


def main():
    os.makedirs(SAMPLE_DIR, exist_ok=True)
    samples = {
        "single.bin": build_single(),
        "stream.bin": build_stream(),
        "compressed.bin": build_compressed(),
        "deep_chain.bin": build_deep_chain(),
        "loop.bin": build_loop(),
        "truncated_rr.bin": build_truncated_rr(),
        "truncated_len.bin": build_truncated_len(),
    }
    for name, data in samples.items():
        path = os.path.join(SAMPLE_DIR, name)
        with open(path, "wb") as f:
            f.write(data)
        print("wrote %s (%d bytes)" % (path, len(data)))


if __name__ == "__main__":
    main()
