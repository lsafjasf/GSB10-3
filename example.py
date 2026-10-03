# -*- coding: utf-8 -*-
"""构造与解析样例：

1. 构造一个"目的不可达 / 端口不可达"差错报文（type=3, code=3），
   随包数据为原始 UDP 报文的前若干字节（奇数长度）。
2. 解析合法报文并打印字段。
3. 篡改 1 个字节后再次解析，观察报错同时给出期望/实际校验和。
4. 截断报文后解析，观察长度不符报错。
"""

from __future__ import annotations

from icmp_error import (
    PROTO_UDP,
    ChecksumMismatchError,
    LengthMismatchError,
    build_error_message,
    parse_error_message,
    verify_checksum,
)


def hexdump(data: bytes) -> str:
    return " ".join("%02x" % b for b in data)


def main() -> None:
    # 1) 构造：模拟随包带回的原 UDP 请求头（故意取奇数长度）
    original_packet = bytes.fromhex(
        "c0a80001c0a800c7d28a0035"  # 13 字节的截断原报文
    )
    msg = build_error_message(
        msg_type=3,
        code=3,
        payload=original_packet,
        protocol=PROTO_UDP,
    )
    print("[1] 构造差错报文（type=3 目的不可达, code=3 端口不可达）")
    print("    报文长度 :", len(msg))
    print("    十六进制 :", hexdump(msg))
    print("    整体校验 :", "通过" if verify_checksum(msg) else "失败")

    # 2) 解析
    print("\n[2] 解析合法报文")
    parsed = parse_error_message(msg)
    print("    类型/代码: %d/%d" % (parsed.type, parsed.code))
    print("    校验和   : 0x%04x" % parsed.checksum)
    print("    长度字段 : %d（实际 %d，自洽）" % (parsed.length, len(msg)))
    print("    协议     : %d (%s)" % (parsed.protocol, parsed.protocol_name))
    print("    随包数据 :", hexdump(parsed.payload))

    # 3) 篡改随包数据 1 个字节
    print("\n[3] 篡改随包数据后解析")
    damaged = bytearray(msg)
    damaged[-1] ^= 0x01
    print("    篡改后   :", hexdump(bytes(damaged)))
    try:
        parse_error_message(bytes(damaged))
    except ChecksumMismatchError as exc:
        print("    捕获异常 : %s: %s" % (type(exc).__name__, exc))
        print("    expected = 0x%04x（报文声明）" % exc.expected)
        print("    actual   = 0x%04x（重算值）" % exc.actual)

    # 4) 长度与声明不符
    print("\n[4] 截断 1 字节后解析")
    try:
        parse_error_message(msg[:-1])
    except LengthMismatchError as exc:
        print("    捕获异常 : %s: %s" % (type(exc).__name__, exc))

    # 5) 全零负载边界
    print("\n[5] 全零负载")
    zero_msg = build_error_message(3, 0, b"\x00" * 8)
    print("    报文     :", hexdump(zero_msg))
    print("    往返解析 :", "通过" if parse_error_message(zero_msg).payload == b"\x00" * 8 else "失败")


if __name__ == "__main__":
    main()
