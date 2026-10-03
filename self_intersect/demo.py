"""演示：相交边对清单、退化处理样例、交点数与边数关系数据。

运行：python3 demo.py
"""

import math
import random

from polygon_self_intersection import (
    find_self_intersections,
    suggest_repairs,
    _fmt,
)


def show(title, points):
    print("=" * 60)
    print(title)
    print("顶点:", points)
    result = find_self_intersections(points)
    if result["removed_duplicates"]:
        print("剔除重复点(零长度边) 原始下标:", result["removed_duplicates"])
    if not result["intersections"]:
        print("无自交。")
    for item in result["intersections"]:
        pts = " ".join(_fmt(p) for p in item["points"])
        print("  边 %d x 边 %d  %-7s 交点: %s"
              % (item["edges"][0], item["edges"][1], item["kind"], pts))
    print("去重交点数:", result["point_count"])
    print("修复建议:")
    for s in suggest_repairs(points):
        print("  -", s)
    print()


def star(n, k):
    """正星形多边形 {n/k} 顶点。"""
    return [(round(math.cos(math.pi / 2 + 2 * math.pi * k * i / n), 12),
             round(math.sin(math.pi / 2 + 2 * math.pi * k * i / n), 12))
            for i in range(n)]


def main():
    # 1. 简单多边形
    show("简单多边形：正方形", [(0, 0), (4, 0), (4, 4), (0, 4)])
    show("简单多边形：凹多边形", [(0, 0), (4, 0), (4, 4), (2, 1), (0, 4)])

    # 2. 自交多边形
    show("自交：蝴蝶结(bowtie)", [(0, 0), (2, 2), (2, 0), (0, 2)])
    show("自交：五角星 {5/2}", star(5, 2))

    # 3. 共线重叠
    show("共线重叠：非相邻边底边重叠",
         [(0, 0), (4, 0), (4, 2), (3, 2), (3, 0), (1, 0), (1, 2), (0, 2)])

    # 4. 退化输入
    show("退化：含重复点/零长度边的正方形",
         [(0, 0), (0, 0), (4, 0), (4, 0), (4, 4), (0, 4), (0, 4)])
    show("退化：相邻边共线回折(spike)，不得误报",
         [(0, 0), (4, 0), (4, 4), (2, 2), (0, 4)])
    show("退化：全部点相同", [(3, 3), (3, 3), (3, 3)])
    show("退化：仅两个点", [(0, 0), (1, 1)])

    # 5. 交点数量与边数的关系
    print("=" * 60)
    print("交点数量与边数 n 的关系")
    print()
    print("理论：非相邻边对数上限 = n(n-3)/2（相交边对数的上界）")
    print("      正星形 {n/k} 的相交边对数 = n(k-1)")
    print("      注：n 为偶数时星形中心附近多线共点，去重交点数 < 边对数")
    print()
    header = "n   k   相交边对数   去重交点数   理论n(k-1)   上界n(n-3)/2"
    print(header)
    print("-" * 58)
    for n, k in [(5, 2), (6, 2), (7, 2), (7, 3), (8, 3), (9, 2),
                 (9, 4), (10, 3), (12, 5)]:
        if math.gcd(n, k) != 1:
            continue
        result = find_self_intersections(star(n, k))
        pairs = len(result["intersections"])
        count = result["point_count"]
        print("%-3d %-3d %-10d %-11d %-12d %d"
              % (n, k, pairs, count, n * (k - 1), n * (n - 3) // 2))
    print()
    print("随机顶点多边形（顶点均匀随机，100 次平均）:")
    print("n     平均交点数   上界n(n-3)/2")
    print("-" * 36)
    rng = random.Random(42)
    for n in (4, 6, 8, 10, 16, 24, 32):
        total = 0
        trials = 100
        for _ in range(trials):
            pts = [(rng.random(), rng.random()) for _ in range(n)]
            total += find_self_intersections(pts)["point_count"]
        print("%-5d %-12.2f %d" % (n, total / trials, n * (n - 3) // 2))


if __name__ == "__main__":
    main()
