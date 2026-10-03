"""生成 data/overlap_case/ 下的重叠处理样例数据。

场景（手工构造，偏移一目了然）：
- v0 基准：6000 字节确定性内容。
- v1：把 [1000,2000) 改为 'A'*1000（段 v1#0），把 [5000,5100) 改为 'B'*100（段 v1#1）。
- v2：把 [1500,2500) 改为 'C'*1000（段 v2#0，与 v1#0 在 [1500,2000) 重叠），
      把 [4980,5120) 改为 'D'*140（段 v2#1，完全覆盖 v1#1），
      末尾追加 100 字节 'TAIL'*25（段 v2#2）。
最新优先恢复后期望：
- v1#0 的 [1500,2000) 被 v2#0 覆盖（部分覆盖）；
- v1#1 被 v2#1 完全覆盖，进入 covered_segments；
- 空洞 [0,1000)、[2500,4980)、[5120,6000) 由 v0 填充。
"""

import json
import os

import diffseg
from diffseg import Backup, Segment

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "overlap_case")


def build():
    base = bytes((i * 7 + 3) % 256 for i in range(6000))

    v1 = base[:1000] + b"A" * 1000 + base[2000:5000] + b"B" * 100 + base[5100:]
    b1 = Backup(
        version="v1", base_version="v0",
        target_length=len(v1), target_digest=diffseg._sha256(v1),
        segments=[
            Segment(base_version="v0", offset=1000, data=b"A" * 1000),
            Segment(base_version="v0", offset=5000, data=b"B" * 100),
        ],
    )
    b1.validate()

    v2 = (v1[:1500] + b"C" * 1000 + v1[2500:4980] + b"D" * 140 + v1[5120:]
          + b"TAIL" * 25)
    b2 = Backup(
        version="v2", base_version="v1",
        target_length=len(v2), target_digest=diffseg._sha256(v2),
        segments=[
            Segment(base_version="v1", offset=1500, data=b"C" * 1000),
            Segment(base_version="v1", offset=4980, data=b"D" * 140),
            Segment(base_version="v1", offset=6000, data=b"TAIL" * 25),
        ],
    )
    b2.validate()
    return base, v1, b1, v2, b2


def main():
    base, v1, b1, v2, b2 = build()

    # 自检：v0->v1->v2 恢复必须逐字节等于 v2。
    data, report = diffseg.restore(base, [b1, b2], base_version="v0")
    assert data == v2, "恢复结果与 v2 不一致"
    mid, _ = diffseg.restore(base, [b1], base_version="v0")
    assert mid == v1, "恢复结果与 v1 不一致"

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "base_v0.bin"), "wb") as fh:
        fh.write(base)
    diffseg.save_backup(b1, os.path.join(OUT, "backup_v1.json"))
    diffseg.save_backup(b2, os.path.join(OUT, "backup_v2.json"))
    with open(os.path.join(OUT, "expected_v2.bin"), "wb") as fh:
        fh.write(v2)
    with open(os.path.join(OUT, "expected_report.json"), "w", encoding="utf-8") as fh:
        json.dump(diffseg.report_to_dict(report), fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(json.dumps(diffseg.report_to_dict(report), ensure_ascii=False, indent=2))
    print(f"\n已写入 {OUT}")


if __name__ == "__main__":
    main()
