#!/usr/bin/env python3
"""
随机往返对拍 + 失败样例收敛（delta debugging 风格的贪心收缩）。

用法：
    python3 roundtrip_fuzz.py [--seed N] [--iterations N] [--out DIR]
    python3 roundtrip_fuzz.py --demo-shrink     # 用内置“坏解码器”演示收敛过程

对拍内容：
  1. 随机 records -> encode -> decode，必须逐字节等于原值（无损往返）。
  2. 编码结果用一个独立的、基于正则的参考解码器再解一遍，
     与主解码器结果交叉比对（differential testing）。
  3. 编码不变量：剥掉规范转义对后，线路字节流中不得残留裸分隔符。

发现失败时：自动运行收缩器，把失败样例一步步缩到最小，
打印每一步收敛过程，并把最小样例写入 --out 目录。
"""

from __future__ import annotations

import argparse
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from escape import decode, encode, encode_field

# --------------------------------------------------------------------------- #
# 独立参考解码器：与 escape.decode 实现路径完全不同（正则驱动），用于对拍
# --------------------------------------------------------------------------- #

# 分组顺序即匹配优先级：
#   1) \xHH  2) \uHHHH / \UHHHHHHHH  3) 八进制  4) 简单转义
#   5) 普通字节  6) 字段分隔符 |  7) 记录分隔符 \n  8) 落单反斜杠（非法）
_TOKEN_RE = re.compile(
    rb"""
      \\x([0-9a-fA-F]{2})
    | \\u([0-9a-fA-F]{4})
    | \\U([0-9a-fA-F]{8})
    | \\([0-7]{1,3})
    | \\([\\|nrtabfv0'"])
    | ([^\\|\n])
    | (\|)
    | (\n)
    | (\\)
    """,
    re.VERBOSE,
)

_SIMPLE = {
    b"\\": b"\\", b"|": b"|", b"n": b"\n", b"r": b"\r", b"t": b"\t",
    b"a": b"\a", b"b": b"\b", b"f": b"\f", b"v": b"\v", b"0": b"\0",
    b"'": b"'", b'"': b'"',
}


def reference_decode(stream: bytes) -> list[list[bytes]]:
    records: list[list[bytes]] = []
    fields: list[bytes] = []
    buf = bytearray()
    pos = 0
    for m in _TOKEN_RE.finditer(stream):
        if m.start() != pos:
            raise ValueError(f"reference decoder: unmatched input at offset {pos}")
        pos = m.end()
        hex2, u4, u8, oct_, simple, plain, pipe, nl, lone = m.groups()
        if hex2 is not None:
            buf.append(int(hex2, 16))
        elif u4 is not None:
            buf.extend(chr(int(u4, 16)).encode("utf-8"))
        elif u8 is not None:
            buf.extend(chr(int(u8, 16)).encode("utf-8"))
        elif oct_ is not None:
            v = int(oct_, 8)
            if v > 0xFF:
                raise ValueError("reference decoder: octal overflow")
            buf.append(v)
        elif simple is not None:
            buf.extend(_SIMPLE[simple])
        elif plain is not None:
            buf.extend(plain)
        elif pipe is not None:
            fields.append(bytes(buf))
            buf.clear()
        elif nl is not None:
            fields.append(bytes(buf))
            buf.clear()
            records.append(fields)
            fields = []
        else:
            raise ValueError("reference decoder: lone backslash")
    if pos != len(stream):
        raise ValueError("reference decoder: trailing garbage")
    if stream == b"":
        return [[b""]]
    fields.append(bytes(buf))
    records.append(fields)
    if stream.endswith(b"\n"):
        records.pop()
    return records


# --------------------------------------------------------------------------- #
# 不变量检查
# --------------------------------------------------------------------------- #

def check_no_raw_delimiters(stream: bytes) -> None:
    """剥掉规范转义对后，线路流里不允许残留裸的 |、\\n、\\r、\\。"""
    stripped = stream
    for esc in (b"\\\\", b"\\|", b"\\n", b"\\r"):
        stripped = stripped.replace(esc, b"")
    # 结构分隔符是 | 和 \n；剥掉转义后剩下的 | / \n 都是结构，合法。
    # 但 \r 和 \ 绝不允许出现。
    for bad, name in ((b"\r", "CR"), (b"\\", "backslash")):
        if bad in stripped:
            raise AssertionError(f"raw {name} leaked into encoded stream: {stream!r}")


def check_roundtrip(records: list[list[bytes]]) -> None:
    stream = encode(records)
    check_no_raw_delimiters(stream)
    got = decode(stream)
    if got != records:
        raise AssertionError(f"roundtrip mismatch: {records!r} -> {stream!r} -> {got!r}")
    ref = reference_decode(stream)
    if ref != got:
        raise AssertionError(f"decoder disagreement: main={got!r} reference={ref!r}")


# --------------------------------------------------------------------------- #
# 随机用例生成
# --------------------------------------------------------------------------- #

# 偏向“危险字节”的字母表：分隔符、反斜杠、NUL、高位字节、转义字母
_HOT_BYTES = b"|\n\r\\0ntxruUabfv'\"0123456789abcdefABCDEF\x00\xff\x7f"


def random_field(rng: random.Random) -> bytes:
    kind = rng.randrange(6)
    if kind == 0:
        return b""                                     # 空字段
    if kind == 1:
        return bytes(rng.choice(b"|\n\r\\") for _ in range(rng.randrange(1, 8)))  # 全是分隔符
    if kind == 2:
        return bytes(rng.getrandbits(8) for _ in range(rng.randrange(1, 64)))
    if kind == 3:
        return bytes(rng.choice(_HOT_BYTES) for _ in range(rng.randrange(1, 48)))
    if kind == 4:
        return bytes(rng.getrandbits(8) for _ in range(rng.randrange(64, 4096)))  # 超长字段
    return bytes(rng.getrandbits(8) for _ in range(rng.randrange(0, 16)))


def random_records(rng: random.Random) -> list[list[bytes]]:
    return [
        [random_field(rng) for _ in range(rng.randrange(1, 8))]
        for _ in range(rng.randrange(1, 6))
    ]


# --------------------------------------------------------------------------- #
# 失败样例收缩器：保持谓词为真的前提下贪心缩小
# --------------------------------------------------------------------------- #

def shrink(records, fails, verbose=True):
    """对失败样例做贪心收缩，返回 (最小样例, 收敛轨迹)。"""
    trace = []

    def size_of(rs):
        return sum(len(f) for r in rs for f in rs and [f] for f in r) or 0

    def total_bytes(rs):
        return sum(len(f) for r in rs for f in r)

    def describe(rs):
        return f"{len(rs)} records, {sum(len(r) for r in rs)} fields, {total_bytes(rs)} bytes"

    step = 0

    def attempt(candidate, why):
        nonlocal step
        if not candidate or any(not r for r in candidate):
            return False
        try:
            fails(candidate)
            return False
        except Exception:
            pass
        step += 1
        records[:] = candidate
        trace.append((step, why, describe(candidate)))
        if verbose:
            print(f"  [shrink #{step:3d}] {why:<28} -> {describe(candidate)}")
        return True

    improved = True
    while improved:
        improved = False
        # 1) 整条记录删除
        for i in range(len(records)):
            if attempt(records[:i] + records[i + 1:], f"drop record {i}"):
                improved = True
                break
        if improved:
            continue
        # 2) 整字段删除
        for ri in range(len(records)):
            for fi in range(len(records[ri])):
                cand = [list(r) for r in records]
                del cand[ri][fi]
                if attempt(cand, f"drop field r{ri}f{fi}"):
                    improved = True
                    break
            if improved:
                break
        if improved:
            continue
        # 3) 字段内容减半（ddmin 风格的 chunk 删除）
        for ri in range(len(records)):
            for fi in range(len(records[ri])):
                field = records[ri][fi]
                if not field:
                    continue
                chunk = max(1, len(field) // 2)
                while chunk >= 1:
                    done = False
                    for start in range(0, len(field), chunk):
                        cand = [list(r) for r in records]
                        cand[ri][fi] = field[:start] + field[start + chunk:]
                        if attempt(cand, f"trim field r{ri}f{fi} chunk={chunk}"):
                            improved = True
                            done = True
                            break
                    if done or chunk == 1:
                        break
                    chunk //= 2
        if improved:
            continue
        # 4) 字节值向更小字母表收敛
        for ri in range(len(records)):
            for fi in range(len(records[ri])):
                field = records[ri][fi]
                for bi, bv in enumerate(field):
                    for small in (b"\x00", b"|", b"\\", b"\n", b"a"):
                        if bv <= small[0]:
                            continue
                        cand = [list(r) for r in records]
                        cand[ri][fi] = field[:bi] + small + field[bi + 1:]
                        if attempt(cand, f"lower byte r{ri}f{fi}[{bi}]"):
                            improved = True
                            break
                    if improved:
                        break
                if improved:
                    break
            if improved:
                break

    return list(map(list, records)), trace


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def dump_case(outdir: str, records) -> None:
    os.makedirs(outdir, exist_ok=True)
    stream = encode(records)
    with open(os.path.join(outdir, "minimal_case.encoded.bin"), "wb") as f:
        f.write(stream)
    with open(os.path.join(outdir, "minimal_case.txt"), "w", encoding="utf-8") as f:
        f.write(f"records = {records!r}\nencoded = {stream!r}\n")


def run_fuzz(seed: int, iterations: int, outdir: str) -> int:
    rng = random.Random(seed)
    print(f"fuzzing: seed={seed} iterations={iterations}")
    for it in range(1, iterations + 1):
        records = random_records(rng)
        try:
            check_roundtrip(records)
        except Exception as exc:
            print(f"\n[FAIL] iteration {it}: {exc}")
            print(f"original failing case: {records!r}")
            print("shrinking...")
            failing = [list(r) for r in records]
            minimal, trace = shrink(failing, check_roundtrip)
            print(f"\nminimal failing case: {minimal!r}")
            print(f"encoded: {encode(minimal)!r}")
            try:
                check_roundtrip(minimal)
            except Exception as e2:
                print(f"minimal case still fails with: {e2}")
            dump_case(outdir, minimal)
            print(f"minimal case written to {outdir}/")
            return 1
        if it % 5000 == 0:
            print(f"  ... {it} iterations OK")
    print(f"OK: {iterations} random cases passed (roundtrip + differential + invariants)")
    return 0


def run_demo_shrink() -> int:
    """演示收敛过程：用一个故意有缺陷的“解码器”制造失败，再自动收缩。

    缺陷设定：假想某旧版解码器遇到 NUL 字节会错位（历史 bug），
    谓词 = “解码结果与假想坏解码器不一致”。收缩器应收敛到 [[b"\\x00"]]。
    """
    def buggy_decode(stream: bytes):
        # 假想坏实现：把 \x00 之后的内容整体丢弃（模拟错位）
        out = decode(stream)
        for rec in out:
            for i, f in enumerate(rec):
                if b"\x00" in f:
                    rec[i] = f.split(b"\x00")[0]
        return out

    def fails(records):
        stream = encode(records)
        if decode(stream) != records:
            raise AssertionError("real decoder broken")
        if buggy_decode(stream) != records:
            raise AssertionError("mismatch vs buggy legacy decoder")

    rng = random.Random(20261003)
    print("demo: searching for a failing case against the buggy legacy decoder...")
    for _ in range(10000):
        records = random_records(rng)
        try:
            fails(records)
        except AssertionError:
            break
    else:
        print("no failing case found (unexpected)")
        return 1
    print(f"original failing case: {records!r}\nshrinking:")
    minimal, _ = shrink([list(r) for r in records], fails)
    print(f"\nminimal failing case: {minimal!r}")
    print(f"encoded: {encode(minimal)!r}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=20261003)
    ap.add_argument("--iterations", type=int, default=20000)
    ap.add_argument("--out", default="failure_cases")
    ap.add_argument("--demo-shrink", action="store_true", help="演示失败样例的收敛过程")
    args = ap.parse_args(argv)
    if args.demo_shrink:
        return run_demo_shrink()
    return run_fuzz(args.seed, args.iterations, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
