#!/usr/bin/env python3
"""恢复对拍脚本。

三种用法：
  python3 compare_restore.py [轮数]
      随机生成“基准 + 多版本备份链”，恢复后与真实目标文件逐字节对拍，
      并重放 data/overlap_case 重叠样例、与期望文件和期望报告对拍。
  python3 compare_restore.py <基准文件> <目标文件>
      对两个真实文件做一次 编码->恢复->逐字节对比。
"""

import json
import os
import random
import sys

import diffseg

HERE = os.path.dirname(os.path.abspath(__file__))
OVERLAP_DIR = os.path.join(HERE, "data", "overlap_case")
BLOCK_SIZES = (16, 64, 256, 1024, 4096)


def random_edit(rng, data):
    op = rng.choice(("mod", "ins", "del", "trunc", "ext", "same"))
    data = bytearray(data)
    if op == "mod" and data:
        pos = rng.randrange(len(data))
        ln = rng.randrange(0, min(200, len(data) - pos) + 1)
        data[pos:pos + ln] = bytes(rng.randrange(256)
                                   for _ in range(rng.randrange(0, 200)))
    elif op == "ins":
        pos = rng.randrange(len(data) + 1)
        data[pos:pos] = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 200)))
    elif op == "del" and data:
        pos = rng.randrange(len(data))
        del data[pos:pos + rng.randrange(1, min(200, len(data) - pos) + 1)]
    elif op == "trunc":
        data = data[:rng.randrange(0, len(data) + 1)]
    elif op == "ext":
        data.extend(bytes(rng.randrange(256) for _ in range(rng.randrange(0, 300))))
    return bytes(data)


def random_round(rounds, seed0=0):
    for rnd in range(rounds):
        rng = random.Random(seed0 + rnd)
        size = rng.choice((0, 1, 7, 100, 4095, 4096, 4097, 20000))
        base = bytes(rng.randrange(256) for _ in range(size)) if size else b""
        versions = [base]
        backups = []
        for step in range(rng.randrange(1, 5)):
            nxt = versions[-1]
            for _ in range(rng.randrange(0, 4)):
                nxt = random_edit(rng, nxt)
            backups.append(diffseg.encode(
                versions[-1], nxt, f"v{step}", f"v{step + 1}",
                block_size=rng.choice(BLOCK_SIZES)))
            versions.append(nxt)
        # 恢复每个链长并逐字节对拍。
        for k in range(1, len(backups) + 1):
            data, report = diffseg.restore(base, backups[:k], base_version="v0")
            assert data == versions[k], f"seed={seed0 + rnd} 链长{k} 字节不一致"
            assert report.digest_ok
        # 全量覆盖场景：不给基准也应能恢复。
        full_target = os.urandom(size) if size else b""
        full = diffseg.encode(base, full_target, "v0", "full",
                              block_size=rng.choice(BLOCK_SIZES))
        data, report = diffseg.restore(None, [full])
        assert data == full_target, f"seed={seed0 + rnd} 全量恢复字节不一致"
        assert report.digest_ok and report.holes == []
    print(f"[1/3] 随机链对拍: {rounds} 轮全部逐字节一致 PASS")


def pairwise(path_a, path_b):
    with open(path_a, "rb") as fa, open(path_b, "rb") as fb:
        base, target = fa.read(), fb.read()
    backup = diffseg.encode(base, target, path_a, path_b)
    data, report = diffseg.restore(base, [backup], base_version=path_a)
    ok = data == target and report.digest_ok
    print(f"基准: {path_a} ({len(base)} 字节)")
    print(f"目标: {path_b} ({len(target)} 字节)")
    print(f"差异段: {len(backup.segments)} 个, 空洞: {len(report.holes)} 个")
    print("逐字节对拍:", "PASS" if ok else "FAIL")
    if not ok:
        sys.exit(1)


def replay_overlap_case():
    with open(os.path.join(OVERLAP_DIR, "base_v0.bin"), "rb") as fh:
        base = fh.read()
    b1 = diffseg.load_backup(os.path.join(OVERLAP_DIR, "backup_v1.json"))
    b2 = diffseg.load_backup(os.path.join(OVERLAP_DIR, "backup_v2.json"))
    with open(os.path.join(OVERLAP_DIR, "expected_v2.bin"), "rb") as fh:
        expected = fh.read()
    with open(os.path.join(OVERLAP_DIR, "expected_report.json"),
              encoding="utf-8") as fh:
        expected_report = json.load(fh)

    data, report = diffseg.restore(base, [b1, b2], base_version="v0")
    assert data == expected, "重叠样例恢复字节不一致"
    assert len(data) == len(expected) == b2.target_length
    assert diffseg.report_to_dict(report) == expected_report, "重叠报告与期望不符"

    print("[2/3] 重叠样例回放 PASS: 长度 {} 字节, 重叠 {} 处, "
          "被完全覆盖的段 {}".format(
              len(data), len(report.overlaps),
              report.covered_segments or "无"))
    print("      重叠样例（最新优先）:")
    for ov in report.overlaps:
        winner = "完全覆盖" if ov.covered_segment in report.covered_segments \
                 else "部分覆盖"
        print(f"        {ov.covered_segment} [{ov.offset},"
              f"{ov.offset + ov.length}) 被 {ov.covering_segment} {winner}")
    print("      空洞显式标记并由基准填充:",
          [(h.offset, h.length, h.filled_from) for h in report.holes])


def boundary_quick_check():
    # 对拍脚本内再快速验证关键边界：无变化 / 空文件 / 缺基准 / 链断裂
    base = bytes(range(256)) * 10
    no_change = diffseg.encode(base, base, "v0", "v1")
    assert diffseg.restore(base, [no_change])[0] == base
    empty = diffseg.encode(b"x", b"", "v0", "v1")
    assert diffseg.restore(b"x", [empty])[0] == b""
    try:
        diffseg.restore(None, [no_change])
    except diffseg.MissingBaseError:
        pass
    else:
        raise AssertionError("缺基准应当报错")
    bad = diffseg.encode(b"a", b"b", "vX", "v2")
    try:
        diffseg.restore(base, [no_change, bad])
    except diffseg.ChainError:
        pass
    else:
        raise AssertionError("链断裂应当报错")
    print("[3/3] 边界快速对拍（无变化/空文件/缺基准/链断裂）PASS")


def main(argv):
    if len(argv) == 3:
        pairwise(argv[1], argv[2])
        return
    rounds = int(argv[1]) if len(argv) == 2 else 300
    random_round(rounds)
    replay_overlap_case()
    boundary_quick_check()
    print("\n全部对拍通过。")


if __name__ == "__main__":
    main(sys.argv)
