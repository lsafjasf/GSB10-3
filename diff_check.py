#!/usr/bin/env python3
"""对拍脚本：分块跨卷读取 vs 整卷拼接读取。

做法（随机化差分测试）：
1. 随机生成原始数据；
2. 用随机卷大小切卷（故意制造各卷长度不一致），并打乱文件名/顺序；
3. 基准 A：逐卷读载荷、按卷头卷号排序后整体拼接；
4. 基准 B：VolumeReader 用各种块大小流式 read()；
5. 逐字节比较 A == B == 原始数据；另对随机 seek 点做切片对拍。

用法：python3 diff_check.py [轮数，默认 200]
退出码 0 表示全部一致。
"""

import os
import random
import shutil
import sys
import tempfile
from pathlib import Path

import mvarchive as mva


def _rename_randomly(d: Path, rng: random.Random) -> None:
    files = list(d.glob("*.bin"))
    rng.shuffle(files)
    for i, p in enumerate(files):
        p.rename(d / f"vol_{rng.randrange(1 << 30):010d}_{i}.bin")


def one_round(rng: random.Random, round_no: int) -> None:
    d = Path(tempfile.mkdtemp(prefix="mva-diff-"))
    try:
        size = rng.choice([0, 1, 2, 3, 7, 63, 64, 65,
                           rng.randrange(0, 5000)])
        data = bytes(rng.randrange(256) for _ in range(size)) if size else b""
        vol_size = rng.randrange(1, 200)
        mva.write_volumes(data, d, max_data_len=vol_size)
        if rng.random() < 0.8:
            _rename_randomly(d, rng)

        with mva.open_volume_stream(d, verify_crc=True) as r:
            # 基准 A：元信息排序后整卷拼接
            baseline = b"".join(
                v.path.read_bytes()[mva.HEADER_SIZE:] for v in r.volumes
            )
            assert baseline == data, f"轮 {round_no}: 拼接结果 != 原始数据"
            assert r.length == len(data), f"轮 {round_no}: length 不一致"

            # 基准 B：分块流式读取，覆盖各种块大小（含 1 与越界值）
            block = rng.choice([1, 2, 3, 5, vol_size - 1, vol_size,
                                vol_size + 1, 127, 256, 1 << 20])
            block = max(1, block)
            streamed = b"".join(iter(lambda: r.read(block), b""))
            assert streamed == baseline, (
                f"轮 {round_no}: 块大小 {block} 时流式读取与整体拼接不一致"
            )

            # 随机 seek 点对拍
            for _ in range(5):
                pos = rng.randrange(0, max(1, len(data) + 1))
                n = rng.choice([1, 2, vol_size, len(data), len(data) + 10])
                r.seek(pos)
                assert r.tell() == pos
                assert r.read(n) == data[pos:pos + n], (
                    f"轮 {round_no}: seek({pos}) read({n}) 不一致"
                )
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    rng = random.Random(20261003)
    for i in range(1, rounds + 1):
        one_round(rng, i)
        if i % 25 == 0:
            print(f"  已对拍 {i}/{rounds} 轮 ...")
    print(f"OK：{rounds} 轮随机对拍全部一致"
          f"（整卷拼接 == 分块跨卷读取 == 原始数据）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
