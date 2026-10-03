#!/usr/bin/env python3
"""重叠处理样例：生成样例数据到 overlap_data/，执行恢复并输出处理结果。

场景：
  基准 v1 为 36 字节文本。
  备份 v1->v2 写入段 A: [8, 24)  = "a"*16
  备份 v2->v3 写入段 B: [12, 28) = "B"*16，与 A 在 [12, 24) 重叠
按“最新优先”，[12, 24) 应取 B 的内容，A 被部分覆盖并记录进报告。
另外附带一个“同备份内段互相覆盖”的非法清单，演示校验拒绝。
"""
import json
import os

from diffseg import Backup, Segment, SegmentOverlapError, restore, sha256_hex

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "overlap_data")


def main():
    os.makedirs(OUT, exist_ok=True)

    base = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"  # v1，36 字节
    seg_a = Segment.from_data("v1", 8, b"a" * 16)   # v2: [8, 24)
    v2 = base[:8] + seg_a.data + base[24:]
    seg_b = Segment.from_data("v2", 12, b"B" * 16)  # v3: [12, 28)，覆盖 A 的 [12,24)
    v3 = v2[:12] + seg_b.data + v2[28:]

    bk2 = Backup("v1", "v2", len(v2), sha256_hex(v2), [seg_a])
    bk3 = Backup("v2", "v3", len(v3), sha256_hex(v3), [seg_b])

    restored, report = restore(base, [bk2, bk3])
    assert restored == v3, "恢复结果与目标不一致"

    # 同备份内段互相覆盖的非法清单
    bad = Backup("v1", "v2", len(v2), sha256_hex(v2),
                 [Segment.from_data("v1", 8, b"a" * 16),
                  Segment.from_data("v1", 16, b"c" * 16)])
    intra = {"file": "intra_overlap.json", "rejected": False, "error": None}
    try:
        Backup.from_json(bad.to_json())
    except SegmentOverlapError as exc:
        intra["rejected"] = True
        intra["error"] = str(exc)

    def dump(name, text):
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    dump("base_v1.bin", base.decode())
    dump("backup_v1_v2.json", bk2.to_json())
    dump("backup_v2_v3.json", bk3.to_json())
    dump("expected_v3.bin", v3.decode())
    dump("intra_overlap.json", bad.to_json())

    result = {
        "scenario": {
            "base": "v1 (36 字节)",
            "segment_A": "v2#seg0 [8, 24) 来自备份 v1->v2",
            "segment_B": "v3#seg0 [12, 28) 来自备份 v2->v3",
            "overlap": "[12, 24)，按最新优先取 B",
        },
        "restored_ok": restored == v3,
        "restored_hex": restored.hex(),
        "restored_text": restored.decode(),
        "report": report.to_dict(),
        "intra_backup_overlap": intra,
    }
    dump("overlap_result.json", json.dumps(result, indent=2, ensure_ascii=False))

    print("基准 v1 :", base.decode())
    print("目标 v3 :", v3.decode())
    print("恢复结果:", restored.decode(), "（逐字节一致: %s）" % (restored == v3))
    print("被覆盖段:")
    for rec in report.overwritten:
        print("  %s 的 [%d, %d) 被 %s 覆盖"
              % (rec.covered_label, *rec.covered_range, rec.by_label))
    print("同备份内重叠校验: rejected=%s -> %s" % (intra["rejected"], intra["error"]))
    print("样例数据已写入 %s/" % OUT)


if __name__ == "__main__":
    main()
