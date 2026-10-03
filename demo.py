"""demo.py — 输出不同平衡容忍度下的割大小数据，并做连通性断言。

运行:  python3 demo.py
"""

from graphcut import (
    assert_cut_valid,
    balanced_min_cut,
    balanced_min_cut_flow,
    global_min_cut,
)


def clique(nodes):
    nodes = list(nodes)
    return [(a, b) for i, a in enumerate(nodes) for b in nodes[i + 1:]]


def show(title, n, edges, tols):
    print(f"\n== {title} (n={n}, m={len(edges)}) ==")
    gsize, *_ = global_min_cut(n, edges)
    print(f"  无约束全局最小割: {gsize}")
    print(f"  {'tol':>4} | {'允许侧大小':>10} | {'割大小':>6} | {'两侧':>7} | 连通性断言")
    for tol in tols:
        size, left, right, ce = balanced_min_cut(n, edges, tol)
        assert_cut_valid(n, edges, left, right, ce)   # 删边后两侧不连通
        lo, hi = (n - tol + 1) // 2, (n + tol) // 2
        print(f"  {tol:>4} | {f'{max(1,lo)}..{min(n-1,hi)}':>10} | "
              f"{size:>6} | {f'{len(left)}|{len(right)}':>7} | PASS")


def main():
    # 例 1：完全图 K6 —— 平衡约束直接决定割大小 k*(n-k)
    show("完全图 K6", 6, clique(range(6)), tols=[0, 2, 4])

    # 例 2：K8 与 K4 由一座桥相连 —— 不平衡时切桥即可，平衡时代价飙升
    show("K8 + K4 单桥", 12,
         clique(range(8)) + clique(range(8, 12)) + [(7, 8)],
         tols=[0, 2, 4, 6, 8])

    # 例 3：两个 K6 社区加 3 条跨社区边 —— 现实“社区发现”式场景
    show("双社区 K6+K6 (3 条跨边)", 12,
         clique(range(6)) + clique(range(6, 12))
         + [(5, 6), (4, 7), (3, 8)],
         tols=[0, 2, 4])

    # 例 4：流模型参数化启发式在大一点图上的表现（n=24 > 精确阈值）
    n = 24
    edges = (clique(range(12)) + clique(range(12, 24))
             + [(11, 12), (10, 13), (9, 14), (8, 15)])
    print(f"\n== 双社区 K12+K12 (4 条跨边), 流模型启发式 (n={n}) ==")
    print(f"  {'tol':>4} | {'割大小':>6} | {'两侧':>9} | 连通性断言")
    for tol in (0, 2, 4, 6, 8):
        size, left, right, ce = balanced_min_cut_flow(n, edges, tol)
        assert_cut_valid(n, edges, left, right, ce)
        print(f"  {tol:>4} | {size:>6} | {f'{len(left)}|{len(right)}':>9} | PASS")


if __name__ == "__main__":
    main()
