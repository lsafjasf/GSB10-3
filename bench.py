"""性能对比基准：建树耗时、查询耗时（BVH vs 暴力）、增量更新 vs 全量重建。

运行：python3 bench.py            # 标准规模
      python3 bench.py --small    # 快速冒烟
"""
import random
import sys
import time

import brute
from bvh import BVH
from scene import (
    aimed_box,
    aimed_ray,
    miss_box,
    move_triangles,
    overlap_scene,
    random_box,
    random_miss_ray,
    random_ray,
    random_scene,
)


def timed(fn, repeat=1):
    best = float("inf")
    for _ in range(repeat):
        t0 = time.perf_counter()
        result = fn()
        dt = time.perf_counter() - t0
        best = min(best, dt)
    return best, result


def fmt(ms):
    return f"{ms:,.2f}"


def bench_build(sizes):
    print("\n## 建树耗时\n")
    print("| 三角形数 | 建树耗时 (ms) | 每个三角形 (us) |")
    print("|---:|---:|---:|")
    for n in sizes:
        tris = random_scene(n, seed=100 + n)
        t, _ = timed(lambda: BVH(tris), repeat=3)
        print(f"| {n:,} | {fmt(t * 1e3)} | {t / n * 1e6:,.2f} |")


def bench_query(n, n_rays, n_boxes, clusters=16, label=""):
    tris = random_scene(n, seed=7, clusters=clusters)
    tree = BVH(tris)
    rng = random.Random(42)
    rays = [random_ray(rng) for _ in range(n_rays)]
    hit_rays = [aimed_ray(rng, tris) for _ in range(n_rays)]
    miss_rays = [random_miss_ray(rng) for _ in range(n_rays)]
    boxes = [random_box(rng) for _ in range(n_boxes)]
    hit_boxes = [aimed_box(rng, tris, half=0.15) for _ in range(n_boxes)]
    miss_boxes = [miss_box(rng) for _ in range(n_boxes)]
    n_ray_hits = sum(len(tree.query_ray(o, d)) for o, d in hit_rays)
    n_box_hits = sum(len(tree.query_aabb(a, b)) for a, b in hit_boxes)
    print(f"（瞄准查询实际命中：射线 {n_ray_hits} 个三角形，包围盒 {n_box_hits} 个）")

    print(f"\n## 查询耗时{label}（{n:,} 个三角形 / {clusters} 簇，{n_rays} 条射线 + {n_boxes} 个包围盒）\n")
    print("| 查询类型 | BVH 总耗时 (ms) | 暴力总耗时 (ms) | 加速比 |")
    print("|---|---:|---:|---:|")

    rows = []
    t_bvh, _ = timed(lambda: [tree.query_ray(o, d) for o, d in rays])
    t_bf, _ = timed(lambda: [brute.ray_query(tris, o, d) for o, d in rays])
    rows.append(("射线（随机方向）", t_bvh, t_bf))

    t_bvh, _ = timed(lambda: [tree.query_ray(o, d) for o, d in hit_rays])
    t_bf, _ = timed(lambda: [brute.ray_query(tris, o, d) for o, d in hit_rays])
    rows.append(("射线（瞄准，必命中）", t_bvh, t_bf))

    t_bvh, _ = timed(lambda: [tree.query_ray_nearest(o, d) for o, d in rays])
    t_bf, _ = timed(lambda: [brute.ray_nearest(tris, o, d) for o, d in rays])
    rows.append(("最近命中射线", t_bvh, t_bf))

    t_bvh, _ = timed(lambda: [tree.query_aabb(a, b) for a, b in boxes])
    t_bf, _ = timed(lambda: [brute.aabb_query(tris, a, b) for a, b in boxes])
    rows.append(("包围盒（随机位置）", t_bvh, t_bf))

    t_bvh, _ = timed(lambda: [tree.query_aabb(a, b) for a, b in hit_boxes])
    t_bf, _ = timed(lambda: [brute.aabb_query(tris, a, b) for a, b in hit_boxes])
    rows.append(("包围盒（瞄准，必命中）", t_bvh, t_bf))

    t_bvh, _ = timed(lambda: [tree.query_ray(o, d) for o, d in miss_rays])
    t_bf, _ = timed(lambda: [brute.ray_query(tris, o, d) for o, d in miss_rays])
    rows.append(("射线（完全落空）", t_bvh, t_bf))

    t_bvh, _ = timed(lambda: [tree.query_aabb(a, b) for a, b in miss_boxes])
    t_bf, _ = timed(lambda: [brute.aabb_query(tris, a, b) for a, b in miss_boxes])
    rows.append(("包围盒（完全落空）", t_bvh, t_bf))

    for name, t_bvh, t_bf in rows:
        speedup = t_bf / t_bvh if t_bvh > 0 else float("inf")
        print(f"| {name} | {fmt(t_bvh * 1e3)} | {fmt(t_bf * 1e3)} | {speedup:,.0f}x |")


def bench_update(n, fractions):
    tris = random_scene(n, seed=8)
    tree = BVH(tris)
    rng = random.Random(123)

    print(f"\n## 增量更新 vs 全量重建（{n:,} 个三角形）\n")
    print("| 移动三角形数 | 增量更新 (ms) | 全量重建 (ms) | 加速比 |")
    print("|---:|---:|---:|---:|")
    for frac in fractions:
        k = max(1, int(n * frac))
        ids = rng.sample(range(n), k)
        updates = move_triangles(tris, ids, (2.0, 1.0, -1.0))
        t_inc, _ = timed(lambda: tree.update(updates), repeat=3)
        # 还原，保证下一轮起点一致
        tree.update([(i, tris[i]) for i in ids])
        t_re, _ = timed(lambda: BVH(tris), repeat=3)
        speedup = t_re / t_inc if t_inc > 0 else float("inf")
        print(f"| {k:,} ({frac:.2%}) | {fmt(t_inc * 1e3)} | {fmt(t_re * 1e3)} | {speedup:,.1f}x |")


def bench_edge(n_overlap):
    print("\n## 边界情形\n")
    print("| 情形 | 操作 | 耗时 (ms) |")
    print("|---|---|---:|")

    tree = BVH([])
    t, _ = timed(lambda: (tree.query_ray((0, 0, 1), (0, 0, -1)),
                          tree.query_aabb((0, 0, 0), (1, 1, 1))), repeat=100)
    print(f"| 空网格 | 射线+包围盒查询 | {fmt(t * 1e3)} |")

    tree = BVH([((0, 0, 0), (1, 0, 0), (0, 1, 0))])
    t, _ = timed(lambda: tree.query_ray((0.2, 0.2, 5), (0, 0, -1)), repeat=100)
    print(f"| 单三角形 | 射线查询 | {fmt(t * 1e3)} |")
    t, _ = timed(lambda: tree.update([(0, ((5, 5, 5), (6, 5, 5), (5, 6, 5)))]), repeat=100)
    print(f"| 单三角形 | 增量更新 | {fmt(t * 1e3)} |")

    tris = overlap_scene(n_overlap)
    t, _ = timed(lambda: BVH(tris))
    print(f"| 大量重叠（{n_overlap:,}） | 建树 | {fmt(t * 1e3)} |")
    tree = BVH(tris)
    o, d = (0.5, 0.5, 10.0), (0.0, 0.0, -1.0)  # 穿过全部重叠三角形
    t, hits = timed(lambda: tree.query_ray(o, d), repeat=10)
    print(f"| 大量重叠（{n_overlap:,}） | 射线查询（命中 {len(hits):,} 个） | {fmt(t * 1e3)} |")

    tris = random_scene(20000, seed=9)
    tree = BVH(tris)
    rng = random.Random(5)
    miss_rays = [random_miss_ray(rng) for _ in range(100)]
    t, _ = timed(lambda: [tree.query_ray(o, d) for o, d in miss_rays])
    print(f"| 查询完全落空（20,000 三角形） | 100 条落空射线 | {fmt(t * 1e3)} |")


def main():
    small = "--small" in sys.argv
    if small:
        bench_build([1_000, 10_000])
        bench_query(n=10_000, n_rays=10, n_boxes=10)
        bench_update(n=10_000, fractions=(0.001, 0.01, 0.1))
        bench_edge(n_overlap=2_000)
    else:
        bench_build([1_000, 10_000, 50_000, 100_000])
        bench_query(n=50_000, n_rays=20, n_boxes=20, clusters=16, label="（密集簇场景）")
        bench_query(n=50_000, n_rays=20, n_boxes=20, clusters=256, label="（分散场景）")
        bench_update(n=50_000, fractions=(0.001, 0.01, 0.05, 0.1))
        bench_edge(n_overlap=10_000)


if __name__ == "__main__":
    main()
