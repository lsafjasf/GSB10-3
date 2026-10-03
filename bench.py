"""bench.py — 写放大测量脚本。

写放大 WA = 磁盘物理写字节数 / 用户逻辑写入字节数。
物理写 = 内存表刷盘字节 + 各层合并输出字节（引擎实时统计）。

运行：python3 bench.py
"""

import shutil
import tempfile

from lsm import LSMEngine

KEYS = 100_000          # 顺序写场景的不同键数
DEEP_KEYS = 500_000     # 深层场景的不同键数（填到 L3）
UPDATES = 100_000       # 更新场景的逻辑写次数
HOT_KEYS = 1000         # 更新场景的不同键数
VALUE_SIZE = 100        # value 长度（字节）


def run_case(name, max_levels, memtable_entries, workload, l0_trigger=4,
             ratio=10, keys=None):
    path = tempfile.mkdtemp(prefix="lsm-bench-")
    try:
        eng = LSMEngine(path, memtable_max_entries=memtable_entries,
                        l0_compaction_trigger=l0_trigger,
                        max_levels=max_levels, size_ratio=ratio)
        if workload == "sequential":
            total = keys or KEYS
            for i in range(total):
                eng.put(("key%08d" % i).encode(), b"v" * VALUE_SIZE)
        elif workload == "update":
            for i in range(UPDATES):
                eng.put(("key%08d" % (i % HOT_KEYS)).encode(), b"v" * VALUE_SIZE)
        eng.flush()
        s = eng.stats()
        print("%-34s levels=%d mem=%-6d L0触发=%d | 逻辑 %8.2f MB 物理 %8.2f MB "
              "WA=%5.2f 刷盘=%4d 合并=%4d" % (
                  name, max_levels, memtable_entries, l0_trigger,
                  s["logical_bytes"] / 1e6, s["physical_bytes"] / 1e6,
                  s["write_amplification"], s["flush_count"], s["compaction_count"]))
        return s
    finally:
        shutil.rmtree(path, True)


def main():
    print("== 场景A：顺序写 %d 个不同键（value=%dB），变化层数 ==" % (KEYS, VALUE_SIZE))
    for levels in (2, 3, 4, 5):
        run_case("顺序写", levels, 1000, "sequential")

    print()
    print("== 场景B：顺序写 %d 个不同键，4 层，变化刷盘阈值 ==" % KEYS)
    for mem in (500, 1000, 4000, 16000):
        run_case("顺序写", 4, mem, "sequential")

    print()
    print("== 场景C：%d 次更新打在 1000 个热点键上（大量重复键）==" % UPDATES)
    for levels in (2, 3, 4, 5):
        run_case("热点更新", levels, 1000, "update")

    print()
    print("== 场景D：4 层 mem=1000，变化 L0 合并触发阈值 ==")
    for trig in (2, 4, 8):
        run_case("顺序写", 4, 1000, "sequential", l0_trigger=trig)

    print()
    print("== 场景E：%d 个不同键 mem=1000 ratio=10（填到 L3，看深层写放大）==" % DEEP_KEYS)
    for levels in (2, 3, 4, 5):
        run_case("深层顺序写", levels, 1000, "sequential", keys=DEEP_KEYS)

    print()
    print("== 场景F：%d 次更新/%d 热点键，小基数容量迫使合并逐层穿透 ==" % (UPDATES, HOT_KEYS))
    for levels, ratio in ((2, 2), (3, 4), (4, 2), (5, 2)):
        run_case("热点穿透更新 r=%d" % ratio, levels, 250, "update", ratio=ratio)


if __name__ == "__main__":
    main()
