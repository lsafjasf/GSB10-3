#!/usr/bin/env python3
"""恢复对拍脚本：随机构造版本链 -> 差分编码 -> 顺序恢复 -> 与目标逐字节对比。

每轮随机进行：原地改写、尾部追加、截断、中部插入、中部删除、无变化；
并对备份链的每个前缀都恢复一次，与对应中间版本严格对拍。
"""
import argparse
import random
import sys

from diffseg import compute_diff, restore


def mutate(rng, data):
    op = rng.choice(["write", "append", "truncate", "insert", "delete", "noop"])
    data = bytearray(data)
    if op == "write" and data:
        off = rng.randrange(len(data))
        n = rng.randrange(1, min(32, len(data) - off) + 1)
        data[off:off + n] = rng.randbytes(n)
    elif op == "append":
        data += rng.randbytes(rng.randrange(1, 33))
    elif op == "truncate" and data:
        del data[rng.randrange(len(data)):]
    elif op == "insert":
        off = rng.randrange(len(data) + 1)
        data[off:off] = rng.randbytes(rng.randrange(1, 33))
    elif op == "delete" and data:
        off = rng.randrange(len(data))
        n = rng.randrange(1, min(32, len(data) - off) + 1)
        del data[off:off + n]
    return bytes(data)


def run_round(rng, rnd):
    versions = [rng.randbytes(rng.randrange(0, 256))]
    chain_len = rng.randrange(1, 5)
    backups = []
    for i in range(chain_len):
        nxt = mutate(rng, versions[-1])
        backups.append(compute_diff(versions[-1], nxt, "v%d" % i, "v%d" % (i + 1)))
        versions.append(nxt)

    # 链内每个前缀版本都必须能从基准精确恢复
    for k in range(1, len(versions)):
        got, report = restore(versions[0], backups[:k])
        if len(got) != len(versions[k]) or got != versions[k]:
            print("FAIL round=%d step=%d: 恢复内容与目标不一致" % (rnd, k), file=sys.stderr)
            print("  期望长度 %d，实际长度 %d" % (len(versions[k]), len(got)), file=sys.stderr)
            print("  报告: %s" % report.to_dict(), file=sys.stderr)
            return False
    return True


def main():
    ap = argparse.ArgumentParser(description="差分编码/恢复随机对拍")
    ap.add_argument("--rounds", type=int, default=200, help="随机轮数（默认 200）")
    ap.add_argument("--seed", type=int, default=20261003, help="随机种子")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    for rnd in range(args.rounds):
        if not run_round(rng, rnd):
            sys.exit(1)
    print("OK: %d 轮对拍全部通过，逐字节一致 (seed=%d)" % (args.rounds, args.seed))


if __name__ == "__main__":
    main()
