"""阈值提前终止 vs 完整计算的对比基准。运行：python3 benchmark.py"""

import random
import time

from edit_distance import Costs, edit_distance, edit_distance_bounded


def bench_pair(a, b, threshold, costs):
    t0 = time.perf_counter()
    full = edit_distance(a, b, costs)
    t1 = time.perf_counter()
    r = edit_distance_bounded(a, b, threshold, costs)
    t2 = time.perf_counter()
    cells_full = len(a) * len(b)
    speedup = (t1 - t0) / max(t2 - t1, 1e-9)
    if not r.exceeded:
        assert r.distance == full, (r.distance, full)
        result = f"{r.distance:.0f}"
    else:
        assert full > threshold, (full, threshold)
        result = f">{threshold}(实际{full:.0f})"
    return (t1 - t0, t2 - t1, cells_full, r.cells, speedup, result)


def main():
    rng = random.Random(2026)
    costs = Costs()
    print("=" * 92)
    print("表 1：相似串（约 5% 差异）—— 距离小，阈值内可返回精确值")
    print("=" * 92)
    header = ("长度", "阈值", "完整耗时ms", "限界耗时ms", "完整单元数",
              "限界单元数", "单元占比", "加速比", "距离")
    print("{:>6} {:>5} {:>10} {:>10} {:>12} {:>12} {:>8} {:>8}  {}".format(*header))
    for n in (500, 1000, 2000, 4000):
        a = [rng.choice("ACGT") for _ in range(n)]
        b = list(a)
        for _ in range(n // 20):  # 约 5% 差异：替换/插入/删除/相邻交换混合
            op = rng.randrange(4)
            pos = rng.randrange(len(b))
            if op == 0:
                b[pos] = rng.choice("ACGT")
            elif op == 1:
                b.insert(pos, rng.choice("ACGT"))
            elif op == 2 and len(b) > 1:
                del b[pos]
            elif pos + 1 < len(b):
                b[pos], b[pos + 1] = b[pos + 1], b[pos]
        for t in (n // 10, n // 4):
            ft, bt, fc, bc, sp, res = bench_pair("".join(a), "".join(b), t, costs)
            print("{:>6} {:>5} {:>10.1f} {:>10.1f} {:>12} {:>12} {:>7.1%} {:>7.1f}x  {}".format(
                n, t, ft * 1e3, bt * 1e3, fc, bc, bc / fc, sp, res))

    print()
    print("=" * 92)
    print("表 2：不相似串（随机独立）—— 距离远超阈值，触发提前终止")
    print("=" * 92)
    print("{:>6} {:>5} {:>10} {:>10} {:>12} {:>12} {:>8} {:>8}  {}".format(*header))
    for n in (500, 1000, 2000, 4000):
        a = "".join(rng.choice("ACGT") for _ in range(n))
        b = "".join(rng.choice("ACGT") for _ in range(n))
        for t in (n // 10, n // 4):
            ft, bt, fc, bc, sp, res = bench_pair(a, b, t, costs)
            print("{:>6} {:>5} {:>10.1f} {:>10.1f} {:>12} {:>12} {:>7.1%} {:>7.1f}x  {}".format(
                n, t, ft * 1e3, bt * 1e3, fc, bc, bc / fc, sp, res))

    print()
    print("=" * 92)
    print("表 3：自定义代价（替换=2, 交换=1, 插入=删除=1）")
    print("=" * 92)
    print("{:>6} {:>5} {:>10} {:>10} {:>12} {:>12} {:>8} {:>8}  {}".format(*header))
    custom = Costs(insert=1, delete=1, substitute=2, transpose=1)
    for n in (1000, 2000):
        a = [rng.choice("ACGT") for _ in range(n)]
        b = list(a)
        for _ in range(n // 20):
            pos = rng.randrange(len(b) - 1)
            b[pos], b[pos + 1] = b[pos + 1], b[pos]  # 只做相邻交换
        for t in (n // 10, n // 4):
            ft, bt, fc, bc, sp, res = bench_pair("".join(a), "".join(b), t, custom)
            print("{:>6} {:>5} {:>10.1f} {:>10.1f} {:>12} {:>12} {:>7.1%} {:>7.1f}x  {}".format(
                n, t, ft * 1e3, bt * 1e3, fc, bc, bc / fc, sp, res))


if __name__ == "__main__":
    main()
