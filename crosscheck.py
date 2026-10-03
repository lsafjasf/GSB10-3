"""对拍脚本：整卷读取（基准） vs 按块读取。

用法：python3 crosscheck.py
每次运行重新生成 ./crosscheck_data/ 下的 7 个乱序命名、不等长卷，比较：
  1. read() 整流读取（基准）
  2. 多种块大小的 iter_chunks
  3. 每个卷边界两侧的跨边界窗口
  4. 大于流长的巨型块与非等长 read 序列
全部与基准逐字节比对，报告写入 crosscheck_report.json，
任何不一致以退出码 1 失败。
"""

import hashlib
import json
import os
import random
import shutil
import sys

from mvarchive import MultiVolumeReader, write_volume

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crosscheck_data")
REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crosscheck_report.json")

# 各卷负载大小：出现 0/1 字节卷，刻意不等长；合计 12345
SIZES = [0, 1, 3000, 1, 5000, 7, 4336]
NAMES = ["p9.arc", "p01.arc", "p17.arc", "p3.arc", "p02.arc", "p20.arc", "p10.arc"]
CHUNK_SIZES = [1, 2, 3, 7, 16, 64, 255, 1000, 4096, 8192, 65536, 1000000]


def generate():
    shutil.rmtree(DATA_DIR, ignore_errors=True)
    os.makedirs(DATA_DIR)
    rng = random.Random(20261003)
    data = bytes(rng.getrandbits(8) for _ in range(sum(SIZES)))
    paths = []
    pos = 0
    for i, size in enumerate(SIZES):
        payload = data[pos:pos + size]
        path = os.path.join(DATA_DIR, NAMES[i])
        write_volume(path, i + 1, len(SIZES), payload)
        paths.append(path)
        pos += size
    return data, paths


def first_diff(expected, got):
    if expected == got:
        return None
    n = min(len(expected), len(got))
    for i in range(n):
        if expected[i] != got[i]:
            return {"offset": i, "expected": expected[i], "got": got[i]}
    return {"offset": n, "expected": "<end>", "got": got[n] if n < len(got) else "<end>"}


def compare(name, expected, got, extra=None):
    row = {
        "case": name,
        "expected_len": len(expected),
        "got_len": len(got),
        "expected_sha256": hashlib.sha256(expected).hexdigest(),
        "got_sha256": hashlib.sha256(got).hexdigest(),
        "match": expected == got,
    }
    if not row["match"]:
        row["first_diff"] = first_diff(expected, got)
    if extra:
        row.update(extra)
    return row


def main():
    expected, paths = generate()
    rows = []

    # 基准：整流读取；同时确认逻辑排序不按文件名字典序
    with MultiVolumeReader(paths) as reader:
        ordered = [os.path.basename(v.source) for v in reader.volumes]
        baseline = reader.read()
    rows.append(compare("baseline_read_all", expected, baseline,
                        extra={"logical_order": ordered,
                               "lex_order_is_different": ordered != sorted(paths)}))

    # 各种块大小
    for chunk in CHUNK_SIZES:
        with MultiVolumeReader(paths) as reader:
            got = b"".join(reader.iter_chunks(chunk))
        rows.append(compare("iter_chunks_%d" % chunk, expected, got))

    # 每个卷边界两侧的跨边界窗口
    bounds = []
    acc = 0
    for size in SIZES[:-1]:
        acc += size
        bounds.append(acc)
    for boundary in bounds:
        start = max(0, boundary - 3)
        for length in (1, 5, 9, 128, 1000):
            with MultiVolumeReader(paths) as reader:
                got = reader.read_window(start, length)
            rows.append(compare("window_off%d_len%d" % (start, length),
                                expected[start:start + length], got))

    # 单块大于整个逻辑流
    with MultiVolumeReader(paths) as reader:
        got = reader.read(10 ** 9)
    rows.append(compare("single_huge_read", expected, got))

    # 非等长 read 序列
    pattern = [3, 1, 10, 2, 77, 500, 4096, 9]
    with MultiVolumeReader(paths) as reader:
        got, i = b"", 0
        while True:
            chunk = reader.read(pattern[i % len(pattern)])
            i += 1
            if not chunk:
                break
            got += chunk
    rows.append(compare("irregular_read_pattern", expected, got))

    failed = [row for row in rows if not row["match"]]
    report = {
        "total_cases": len(rows),
        "passed": len(rows) - len(failed),
        "failed": len(failed),
        "stream_size": len(expected),
        "cases": rows,
    }
    with open(REPORT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    print("逻辑顺序（按卷号，非字典序）:")
    print("  " + " -> ".join(ordered))
    print("流大小: %d 字节 | 对拍用例: %d  通过: %d  失败: %d"
          % (len(expected), report["total_cases"], report["passed"], report["failed"]))
    for row in rows:
        mark = "PASS" if row["match"] else "FAIL"
        print("  [%s] %-32s len=%d sha256=%s"
              % (mark, row["case"], row["got_len"], row["got_sha256"][:12]))
    print("报告已写入 %s" % REPORT)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
