"""缺口检测样例：python3 gap_demo.py

生成 5 卷乱序命名的归档，故意抽走第 3 卷，演示：
  1. 打开归档时立即抛出 MissingVolumeError（不静默跳过）
  2. 从异常中读出缺口位置（缺失卷号 + 逻辑字节偏移）
  3. 补齐缺失卷后正常顺序读取
"""

import os
import shutil
import sys

from mvarchive import MissingVolumeError, MultiVolumeReader, write_volume

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO_DIR = os.path.join(HERE, "gap_demo_data")

# 文件名刻意乱序：字典序 z_* 与卷号顺序完全无关
NAMES = ["z_tail.vol", "z_head.vol", "z_mid.vol", "z_02.vol", "z_04.vol"]
MISSING = 3  # 抽走第 3 卷


def build_archive():
    shutil.rmtree(DEMO_DIR, ignore_errors=True)
    os.makedirs(DEMO_DIR)
    parts = []
    for i in range(5):
        # 每卷是一段可打印文本，长度不等
        text = ("[volume %d payload] " % (i + 1)) * (i + 2)
        parts.append(text.encode("ascii"))
    paths = []
    for i, name in enumerate(NAMES):
        if i + 1 == MISSING:
            continue  # 模拟传输中丢失的一卷
        path = os.path.join(DEMO_DIR, name)
        write_volume(path, i + 1, 5, parts[i])
        paths.append(path)
    return parts, paths


def main():
    parts, paths = build_archive()
    print("已生成归档目录: %s" % DEMO_DIR)
    print("磁盘上的卷文件（缺第 %d 卷）:" % MISSING)
    for name in sorted(os.listdir(DEMO_DIR)):
        print("  %s" % name)

    print("\n--- 1. 尝试打开不完整归档 ---")
    try:
        MultiVolumeReader(paths)
        print("意外：没有报错！")
        return 1
    except MissingVolumeError as err:
        print("检测到缺失，拒绝读取：")
        print("  %s" % err)

    print("\n--- 2. 程序化定位缺口 ---")
    try:
        MultiVolumeReader(paths)
    except MissingVolumeError as err:
        print("  总卷数: %d" % err.total)
        print("  缺失卷号: %s" % (list(err.missing_volumes),))
        for gap in err.gaps:
            print("  缺口: 卷 %d-%d, 逻辑字节偏移 %d"
                  % (gap.first, gap.last, gap.byte_offset))
        # 校验偏移：缺口应紧跟卷 1、2 的负载之后
        expect = sum(len(parts[i]) for i in range(MISSING - 1))
        assert err.gaps[0].byte_offset == expect, "缺口偏移计算错误"
        print("  （偏移 %d = 卷1+卷2 负载长度之和，校验通过）" % expect)

    print("\n--- 3. 补齐缺失卷后顺序读取 ---")
    repair = os.path.join(DEMO_DIR, NAMES[MISSING - 1])
    write_volume(repair, MISSING, 5, parts[MISSING - 1])
    paths.append(repair)
    with MultiVolumeReader(paths) as reader:
        print("  逻辑顺序: %s"
              % " -> ".join(os.path.basename(v.source) for v in reader.volumes))
        print("  逻辑流总长: %d 字节" % reader.logical_size)
        data = reader.read()
    assert data == b"".join(parts), "补齐后的数据与原始数据不一致"
    print("  拼接结果与原始数据逐字节一致，流预览:")
    print("  %r ..." % data[:60])
    print("\n样例运行完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
