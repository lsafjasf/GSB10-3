"""随机数据往返对拍脚本（仅标准库）。

对任意字节序列组成的 记录/字段 做 encode -> decode，逐字节比较。
一旦发现失败样例，自动做 delta-debugging 风格的收敛（缩小），
打印每一步收敛过程，并把最小失败样例落盘。

用法:
  python3 fuzz_roundtrip.py                # 默认 20000 轮，随机种子
  python3 fuzz_roundtrip.py --seed 42 --trials 50000
  python3 fuzz_roundtrip.py --demo-bug     # 注入一个刻意写错的解码器，演示收敛过程
"""

import argparse
import copy
import random
import sys

from escape_codec import (
    EscapeError,
    encode_payload,
    decode_payload,
)

SPECIAL_BYTES = [
    0x5C, 0x7C, 0x0A, 0x0D, 0x09, 0x00,
    ord("n"), ord("r"), ord("t"), ord("0"), ord("x"), ord("X"),
]


def random_field(rng, max_len=64):
    n = rng.randrange(0, max_len + 1)
    if rng.random() < 0.4:
        # 偏向特殊字节（转义符、分隔符、换行、形似转义的字母）
        return bytes(rng.choice(SPECIAL_BYTES) for _ in range(n))
    return bytes(rng.randrange(256) for _ in range(n))


def random_records(rng):
    records = []
    for _ in range(rng.randrange(0, 6)):
        records.append([
            random_field(rng) for _ in range(rng.randrange(1, 7))
        ])
    return records


def buggy_decode_payload(payload):
    """刻意写错的解码器：把 \\n 误当作字面字母 'n'（吞掉反斜杠）。

    仅用于 --demo-bug 演示失败样例的自动收敛。"""
    lines = payload.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    out_lines = []
    for line in lines:
        fields, field = [], bytearray()
        i = 0
        while i < len(line):
            if line[i] == 0x5C and i + 1 < len(line) and line[i + 1] == ord("n"):
                field.append(ord("n"))  # BUG：本应解码成 0x0A
                i += 2
            elif line[i] == 0x7C:
                fields.append(bytes(field)); field.clear(); i += 1
            else:
                field.append(line[i]); i += 1
        fields.append(bytes(field))
        out_lines.append(fields)
    return out_lines


def verify_roundtrip(records, decoder):
    """返回 True 表示仍然失败（往返不一致或抛异常）。"""
    try:
        payload = encode_payload(records)
        got = decoder(payload)
        return got != records
    except EscapeError:
        return True


def _size(records):
    return (len(records),
            sum(len(r) for r in records),
            sum(len(f) for r in records for f in r))


def shrink(failing_records, decoder):
    """贪心 delta-debugging：每一步只接受仍然失败的更小输入。"""
    current = copy.deepcopy(failing_records)
    steps = []
    while True:
        moved = False
        # 1) 删除整条记录
        for i in range(len(current)):
            cand = current[:i] + current[i + 1:]
            if cand and verify_roundtrip(cand, decoder):
                steps.append(("drop record #%d" % i, cand))
                current = cand
                moved = True
                break
        if moved:
            continue
        # 2) 删除某个字段
        for ri, rec in enumerate(current):
            if len(rec) <= 1:
                continue
            for fi in range(len(rec)):
                cand = copy.deepcopy(current)
                cand[ri].pop(fi)
                if verify_roundtrip(cand, decoder):
                    steps.append(("drop field r%d.#%d" % (ri, fi), cand))
                    current = cand
                    moved = True
                    break
            if moved:
                break
        if moved:
            continue
        # 3) 把某个字段砍成前半 / 后半
        for ri, rec in enumerate(current):
            for fi, field in enumerate(rec):
                if len(field) < 2:
                    continue
                half = len(field) // 2
                for label, piece in (("prefix", field[:half]), ("suffix", field[half:])):
                    cand = copy.deepcopy(current)
                    cand[ri][fi] = piece
                    if verify_roundtrip(cand, decoder):
                        steps.append(("shrink r%d.f%d to %s (%d->%d bytes)"
                                      % (ri, fi, label, len(field), len(piece)), cand))
                        current = cand
                        moved = True
                        break
                if moved:
                    break
            if moved:
                break
        if moved:
            continue
        # 4) 逐字节删除
        for ri, rec in enumerate(current):
            for fi, field in enumerate(rec):
                if not field:
                    continue
                for bi in range(len(field)):
                    cand = copy.deepcopy(current)
                    cand[ri][fi] = field[:bi] + field[bi + 1:]
                    if verify_roundtrip(cand, decoder):
                        steps.append(("remove byte r%d.f%d[%d]" % (ri, fi, bi), cand))
                        current = cand
                        moved = True
                        break
                if moved:
                    break
            if moved:
                break
        if not moved:
            return current, steps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--trials", type=int, default=20000)
    ap.add_argument("--demo-bug", action="store_true",
                    help="用刻意写错的解码器演示失败收敛")
    args = ap.parse_args()

    seed = args.seed
    if seed is None:
        seed = random.randrange(1 << 31)
    rng = random.Random(seed)
    decoder = buggy_decode_payload if args.demo_bug else decode_payload

    print("seed=%d trials=%d demo_bug=%s" % (seed, args.trials, args.demo_bug))
    failing = None
    for trial in range(1, args.trials + 1):
        records = random_records(rng)
        if verify_roundtrip(records, decoder):
            failing = records
            print("FAIL at trial %d" % trial)
            break
        if trial % 5000 == 0:
            print("  ... %d trials passed" % trial)

    if failing is None:
        print("ALL %d TRIALS PASSED (逐字节往返一致)" % args.trials)
        return 0

    print("\n原始失败样例 (records=%d):" % len(failing))
    for rec in failing:
        print("  ", [f.hex() for f in rec])

    minimal, steps = shrink(failing, decoder)
    print("\n收敛过程 (%d 步):" % len(steps))
    for idx, (action, cand) in enumerate(steps, start=1):
        print("  step %2d: %-40s -> %s" % (idx, action, _size(cand)))
    print("\n最小失败样例:")
    for rec in minimal:
        print("  ", [f for f in rec])
        print("   hex:", [f.hex() for f in rec])
    print("编码结果:", encode_payload(minimal))
    with open("failure_case.bin", "wb") as fh:
        fh.write(encode_payload(minimal))
    print("已写入 failure_case.bin")
    return 1


if __name__ == "__main__":
    sys.exit(main())
