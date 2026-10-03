#!/usr/bin/env python3
"""缺口检测样例。

构造 5 个卷（文件名故意打乱且与卷号无关），删除第 2、3 卷，
展示读取方如何：按卷头卷号排序、发现缺口、定位缺口两侧卷号与
逻辑字节偏移，而不是静默跳过继续读。

运行：python3 demo_gap.py
"""

import shutil
import tempfile
from pathlib import Path

import mvarchive as mva

FAKE_NAMES = {1: "z_last.bin", 2: "m_mid.bin", 3: "a_first.bin",
              4: "q_04.bin", 5: "q_05.bin"}


def main() -> int:
    d = Path(tempfile.mkdtemp(prefix="mva-gap-demo-"))
    try:
        # 每卷载荷长度不同（卷1=100B, 卷2=200B ...），验证偏移按卷头长度累计
        import zlib
        for no in range(1, 6):
            body = bytes([no + 64]) * (no * 100)
            header = mva.HEADER.pack(
                mva.MAGIC, mva.VERSION, 0, no, 5, len(body),
                zlib.crc32(body) & 0xFFFFFFFF,
            )
            (d / FAKE_NAMES[no]).write_bytes(header + body)

        print("卷目录中的文件（文件名字典序与逻辑顺序无关）：")
        for p in sorted(d.iterdir()):
            print("  ", p.name)

        print("\n删除卷 2 和卷 3 后尝试按顺序读取 ...")
        (d / FAKE_NAMES[2]).unlink()
        (d / FAKE_NAMES[3]).unlink()

        metas = mva.discover_volumes(d)
        print("按卷头卷号排序后实际存在：",
              [m.vol_no for m in metas])

        gaps = mva.find_gaps(metas)
        for g in gaps:
            print("  检测到 ->", g.describe())

        try:
            with mva.open_volume_stream(d) as r:
                r.read()
        except mva.MissingVolumeError as e:
            print("\n打开读取流时拒绝继续（未静默跳过）：")
            print(" ", e)
            return 0
        print("错误：不应走到这里")
        return 1
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
