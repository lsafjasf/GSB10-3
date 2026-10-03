# -*- coding: utf-8 -*-
"""标准测试向量对拍脚本。

用两套互相独立的实现逐字节比对：
  - icmp_error.internet_checksum：主实现（逐字节拼字 + 每步回绕进位）
  - reference_checksum：独立参考实现（struct 批量取字 + 末尾统一回绕）

标准向量来源：
  V1  RFC 1071 §4.1（反码求和经典示例，期望 0x220d）
  V2  RFC 791 §3.1 IPv4 首部示例（期望 0xb861）
  V3  奇数长度 "abcde"（末尾补 0x00，期望 0xd638）
  V4  全零负载 8 字节（反码为 0xffff，即 -0）
  V5  单字节 0xff（补零为 ff00，期望 0x00ff）

退出码：全部一致返回 0，任一不一致返回 1。
"""

from __future__ import annotations

import struct
import sys

from icmp_error import internet_checksum


def reference_checksum(data: bytes) -> int:
    """与主实现写法不同的独立参考实现，仅用于对拍。"""
    if len(data) & 1:
        data = data + b"\x00"
    words = struct.unpack("!%dH" % (len(data) // 2), data)
    total = sum(words)
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return total ^ 0xFFFF


VECTORS = [
    (
        "V1 RFC1071 §4.1",
        bytes.fromhex("0001f203f4f5f6f7"),
        0x220D,
    ),
    (
        "V2 RFC791 IPv4 首部",
        bytes.fromhex("450000730000400040110000c0a80001c0a800c7"),
        0xB861,
    ),
    (
        "V3 奇数长度 abcde",
        b"abcde",
        0xD638,
    ),
    (
        "V4 全零 8 字节",
        bytes.fromhex("0000000000000000"),
        0xFFFF,
    ),
    (
        "V5 单字节 ff",
        bytes.fromhex("ff"),
        0x00FF,
    ),
]


def hexdump(data: bytes) -> str:
    return " ".join("%02x" % b for b in data)


def main() -> int:
    failures = 0
    for name, data, std_expected in VECTORS:
        got = internet_checksum(data)
        ref = reference_checksum(data)
        got_bytes = struct.pack("!H", got)
        ref_bytes = struct.pack("!H", ref)
        std_bytes = struct.pack("!H", std_expected)
        byte_match = got_bytes == ref_bytes == std_bytes

        print("=" * 68)
        print(name)
        print("  输入       :", hexdump(data))
        print("  标准期望   : %04x  字节: %s" % (std_expected, hexdump(std_bytes)))
        print("  主实现     : %04x  字节: %s" % (got, hexdump(got_bytes)))
        print("  参考实现   : %04x  字节: %s" % (ref, hexdump(ref_bytes)))

        # 逐字节展示比对结果
        for idx, (b_std, b_got, b_ref) in enumerate(
            zip(std_bytes, got_bytes, ref_bytes)
        ):
            ok = b_std == b_got == b_ref
            print(
                "  校验和字节%d : std=%02x main=%02x ref=%02x -> %s"
                % (
                    idx,
                    b_std,
                    b_got,
                    b_ref,
                    "一致" if ok else "不一致",
                )
            )

        print("  结果       : %s" % ("PASS" if byte_match else "FAIL"))
        if not byte_match:
            failures += 1

    print("=" * 68)
    if failures:
        print("对拍失败：%d 个向量不一致" % failures)
        return 1
    print("全部 %d 个标准向量逐字节一致，对拍通过" % len(VECTORS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
