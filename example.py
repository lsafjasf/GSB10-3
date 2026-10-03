#!/usr/bin/env python3
"""构造与解析样例：造一个“目的不可达”差错报文，再解析回来，
最后演示篡改校验和后解析器如何报告期望值与实际值。"""

from errpkt import (ChecksumMismatchError, build_packet, parse_packet)

# 1. 构造：Type=3（目的不可达）, Code=1（主机不可达）, 随包数据为原始数据报摘录
original_datagram_excerpt = bytes.fromhex("4500003c1c4640004006b1e6c0a80001c0a800c7")
packet = build_packet(msg_type=3, code=1, payload=original_datagram_excerpt)
print(f"构造的报文 ({len(packet)} 字节): {packet.hex(' ')}")

# 2. 解析：校验 Length / Protocol / Checksum 全部自洽
pkt = parse_packet(packet)
print(f"解析结果: type={pkt.type} code={pkt.code} "
      f"checksum=0x{pkt.checksum:04X} length={pkt.length} "
      f"protocol={pkt.protocol}")
print(f"随包数据: {pkt.payload.hex(' ')}")

# 3. 篡改校验和，观察解析器报出的期望值与实际值
tampered = packet[:2] + b"\x12\x34" + packet[4:]
try:
    parse_packet(tampered)
except ChecksumMismatchError as exc:
    print(f"捕获到篡改: {exc}")
    print(f"  期望值（按收到字节重算）: 0x{exc.expected:04X}")
    print(f"  实际值（报文中携带）    : 0x{exc.actual:04X}")
