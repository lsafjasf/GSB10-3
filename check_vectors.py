#!/usr/bin/env python3
"""标准测试向量对拍脚本。

向量来源：
1. RFC 1071 第 3 节的反码求和示例。
2. 经典 IPv4 首部校验和示例（RFC 791 首部 + 手算/教科书结果 0xB861）。
3. 奇数长度、全零负载的手算向量。
4. 完整差错报文的逐字节 golden 向量（构造结果必须与给定字节完全一致）。
"""

import sys

from errpkt import (PROTO_ICMP, build_packet, ones_complement_checksum,
                    parse_packet)

CHECKSUM_VECTORS = [
    # (描述, 输入字节, 期望校验和)
    ("RFC 1071 §3 示例",
     bytes.fromhex("0001f203f4f5f6f7"), 0x220D),
    ("IPv4 首部经典示例（RFC 791 风格）",
     bytes.fromhex("450000730000400040110000c0a80001c0a800c7"), 0xB861),
    ("奇数长度（末尾补零）",
     bytes.fromhex("010203"), 0xFBFD),
    ("全零负载（10 字节）",
     b"\x00" * 10, 0xFFFF),
    ("空输入",
     b"", 0xFFFF),
]

PACKET_VECTORS = [
    # (描述, type, code, payload, 期望完整报文字节)
    ("Type=3 Code=1 偶数负载", 3, 1, bytes.fromhex("deadbeef"),
     bytes.fromhex("03015e55000c0100deadbeef")),
    ("Type=11 Code=0 奇数负载", 11, 0, bytes.fromhex("010203"),
     bytes.fromhex("0b00eff2000b0100010203")),
    ("Type=0 Code=0 全零负载", 0, 0, b"\x00" * 8,
     bytes.fromhex("0000feef00100100") + b"\x00" * 8),
]


def main():
    failures = 0

    print("== 校验和向量 ==")
    for desc, data, expected in CHECKSUM_VECTORS:
        actual = ones_complement_checksum(data)
        ok = actual == expected
        failures += not ok
        print(f"[{'OK' if ok else 'FAIL'}] {desc}: "
              f"期望 0x{expected:04X}, 实际 0x{actual:04X}")

    print("\n== 完整报文逐字节向量 ==")
    for desc, msg_type, code, payload, expected_bytes in PACKET_VECTORS:
        built = build_packet(msg_type, code, payload, protocol=PROTO_ICMP)
        ok = built == expected_bytes
        failures += not ok
        print(f"[{'OK' if ok else 'FAIL'}] {desc}")
        print(f"       期望: {expected_bytes.hex(' ')}")
        print(f"       实际: {built.hex(' ')}")
        if ok:
            pkt = parse_packet(built)
            roundtrip = (pkt.type == msg_type and pkt.code == code
                         and pkt.payload == payload)
            failures += not roundtrip
            print(f"       解析回环: {'OK' if roundtrip else 'FAIL'}")

    print(f"\n结果: {'全部通过' if failures == 0 else f'{failures} 项失败'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
