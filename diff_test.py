#!/usr/bin/env python3
"""逐字段对拍：socks5.reply（主解析器） vs reference（字节手写参考实现）。

语料手工拼字节，覆盖：IPv4 / 域名 / IPv6、域名长度边界（1、255、超长截断）、
头部与各字段截断、未知应答码、未知地址类型、错误版本、尾部多余字节。

输出每条用例的字段级对比，全部一致退出码 0，否则 1。
"""

from __future__ import annotations

import sys

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
from reference import RefError, parse_reply_reference


def rep_reply(rep: int, atyp: int, addr: bytes, port: int) -> bytes:
    return bytes([0x05, rep, 0x00, atyp]) + addr + port.to_bytes(2, "big")


def ipv4(rep: int = 0, ip: tuple[int, int, int, int] = (1, 2, 3, 4), port: int = 0x0438) -> bytes:
    return rep_reply(rep, ATYP_IPV4, bytes(ip), port)


def ipv6(rep: int = 0, groups: tuple[int, ...] | None = None, port: int = 0x01BB) -> bytes:
    if groups is None:
        groups = (0x2001, 0xDB8, 0, 0, 0, 0, 0, 1)
    raw = b"".join(g.to_bytes(2, "big") for g in groups)
    return rep_reply(rep, ATYP_IPV6, raw, port)


def domain(rep: int, label: bytes, port: int = 80) -> bytes:
    return rep_reply(rep, ATYP_DOMAIN, bytes([len(label)]) + label, port)


# ---------- 语料：name, bytes, 期望说明 ----------
def build_cases():
    cases = []

    # 三种地址类型（成功）
    cases.append(("ipv4/ok", ipv4(), None))
    cases.append(("ipv6/ok/compressed", ipv6(), None))
    cases.append(("ipv6/ok/loopback", ipv6(groups=(0, 0, 0, 0, 0, 0, 0, 1), port=1080), None))
    cases.append(("ipv6/ok/full", ipv6(groups=(0x2001, 0xDB8, 0, 0, 0x1, 0x2, 0x3, 0x4), port=443), None))
    cases.append(("ipv6/ok/trailing-zeros", ipv6(groups=(0xFE80, 0, 0, 0, 0, 0, 0, 0), port=22), None))
    cases.append(("domain/ok", domain(0, b"example.com", 8080), None))

    # 全部已知失败码（地址类型混搭）
    for code in range(1, 9):
        cases.append((f"rep/fail/0x{code:02x}/ipv4", ipv4(rep=code), None))
    cases.append(("rep/fail/0x02/domain", domain(2, b"blocked.example", 80), None))
    cases.append(("rep/fail/0x08/ipv6", ipv6(rep=8), None))

    # 域名长度边界：1 字节、255 字节
    cases.append(("domain/len=0/empty-label", domain(0, b"", 80), None))
    cases.append(("domain/len=1", domain(0, b"a", 1), None))
    label255 = b"x" * 255
    cases.append(("domain/len=255", domain(0, label255, 65535), None))

    full = domain(0, b"example.com", 80)

    # 截断：头部
    for cut in range(0, 4):
        cases.append((f"trunc/header/{cut}B", ipv4()[:cut], "truncated"))

    # 截断：IPv4 地址段（4 字节，逐个切）
    for have in range(0, 4):
        cases.append((f"trunc/ipv4-addr/{have}of4", ipv4()[:4 + have], "truncated"))

    # 截断：域名长度字节缺失
    cases.append(("trunc/domain/no-length", rep_reply(0, ATYP_DOMAIN, b"", 0)[:4], "truncated"))
    # 截断：域名实际字节少于长度声明（边界：255 声明但 0/254 到达）
    cases.append(("trunc/domain/decl255/0",
                  bytes([0x05, 0, 0, ATYP_DOMAIN, 255]), "truncated"))
    cases.append(("trunc/domain/decl255/254",
                  bytes([0x05, 0, 0, ATYP_DOMAIN, 255]) + label255[:254], "truncated"))
    cases.append(("trunc/domain/normal/short",
                  full[:-3], "truncated"))

    # 截断：IPv6 地址段
    cases.append(("trunc/ipv6-addr/1of16", ipv6()[:5], "truncated"))
    cases.append(("trunc/ipv6-addr/15of16", ipv6()[:4 + 15], "truncated"))

    # 截断：端口
    cases.append(("trunc/ipv4-port/0of2", ipv4()[:-2], "truncated"))
    cases.append(("trunc/ipv4-port/1of2", ipv4()[:-1], "truncated"))
    cases.append(("trunc/domain-port/1of2", full[:-1], "truncated"))

    # 未知应答码（绝不能当成功）
    for code in (0x09, 0x10, 0x7F, 0xFF):
        cases.append((f"unknown-rep/0x{code:02x}/ipv4", ipv4(rep=code), "unknown_rep"))
    cases.append(("unknown-rep/0x09/domain", domain(0x09, b"x", 80), "unknown_rep"))

    # 未知地址类型
    for atyp in (0x00, 0x02, 0x05, 0xFF):
        raw = bytes([0x05, 0, 0, atyp, 0, 0, 0, 0, 0, 0])
        cases.append((f"unknown-atyp/0x{atyp:02x}", raw, "unknown_atyp"))

    # 错误版本
    cases.append(("bad-version/04", bytes([0x04, 0, 0, 1, 1, 2, 3, 4, 0, 80]), "bad_version"))
    cases.append(("bad-version/00", bytes([0x00, 0, 0, 1, 1, 2, 3, 4, 0, 80]), "bad_version"))

    # 尾部多余字节（代理粘包时常见）：两个解析器都应忽略尾部
    cases.append(("trailing-garbage/ipv4", ipv4() + b"\x00\x00extra", None))
    cases.append(("trailing-garbage/domain", domain(0, b"ab", 80) + b"\xff" * 5, None))

    return cases


MAIN_ERROR_KIND = {
    TruncatedReply: "truncated",
    UnknownReplyCode: "unknown_rep",
    UnknownAddressType: "unknown_atyp",
    BadVersion: "bad_version",
}


def run_main(data: bytes):
    try:
        r = parse_reply(data)
        return "ok", {
            "ver": r.ver, "rep": r.rep, "rsv": r.rsv, "atyp": r.atyp,
            "bnd_addr": r.bnd_addr, "bnd_port": r.bnd_port,
        }
    except Exception as exc:  # noqa: BLE001 - 对拍就是要捕获全部差异
        for cls, kind in MAIN_ERROR_KIND.items():
            if isinstance(exc, cls):
                return kind, None
        return type(exc).__name__, None


def run_ref(data: bytes):
    try:
        return "ok", parse_reply_reference(data)
    except RefError as exc:
        return exc.kind, None


def main() -> int:
    cases = build_cases()
    failures = 0
    print(f"{'case':36} {'main':14} {'reference':14} result")
    print("-" * 78)
    for name, data, expected_kind in cases:
        mkind, mfields = run_main(data)
        rkind, rfields = run_ref(data)

        match = mkind == rkind
        detail = ""
        if match and mkind == "ok":
            diffs = [k for k in mfields if mfields[k] != rfields[k]]
            match = not diffs
            if diffs:
                detail = "field diff: " + ",".join(
                    f"{k} main={mfields[k]!r} ref={rfields[k]!r}" for k in diffs
                )
        if expected_kind is not None:
            if mkind != expected_kind:
                match = False
                detail += f" [expected kind {expected_kind}]"

        status = "MATCH" if match else "DIFF!"
        if not match:
            failures += 1
        print(f"{name:36} {mkind:14} {rkind:14} {status} {detail}")

    print("-" * 78)
    print(f"{len(cases)} cases, {failures} mismatch(es)")

    # 附：三条代表性成功用例的逐字段明细
    print("\nfield-level detail (success samples):")
    for name, data, _ in cases:
        if name in ("ipv4/ok", "domain/ok", "ipv6/ok/compressed"):
            _, m = run_main(data)
            _, r = run_ref(data)
            print(f"  [{name}] hex={data.hex()}")
            for k in ("ver", "rep", "rsv", "atyp", "bnd_addr", "bnd_port"):
                same = "=" if m[k] == r[k] else "!"
                print(f"    {k:9} main={m[k]!r:24} {same} ref={r[k]!r}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
