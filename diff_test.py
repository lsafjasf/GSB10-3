"""对拍测试：BVH 查询结果必须与全量暴力检测完全一致。

覆盖：空网格、单三角形、大量重叠、退化三角形、查询完全落空、
增量更新后的一致性（更新后的 BVH vs 暴力 vs 全量重建的 BVH）。

运行：python3 diff_test.py
"""
import random

import brute
from bvh import BVH
from scene import (
    miss_box,
    move_triangles,
    overlap_scene,
    random_box,
    random_miss_ray,
    random_ray,
    random_scene,
)

FAILURES = []
CHECKS = 0


def check(name, got, want):
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILURES.append((name, got, want))
        print(f"  FAIL {name}: got={got!r} want={want!r}")


def compare_queries(name, tris, tree, rng, n_rays, n_boxes, n_miss):
    for k in range(n_rays):
        o, d = random_ray(rng)
        check(f"{name}/ray#{k}", tree.query_ray(o, d), brute.ray_query(tris, o, d))
        check(f"{name}/ray_nearest#{k}",
              tree.query_ray_nearest(o, d), brute.ray_nearest(tris, o, d))
    for k in range(n_boxes):
        qmin, qmax = random_box(rng)
        check(f"{name}/aabb#{k}",
              tree.query_aabb(qmin, qmax), brute.aabb_query(tris, qmin, qmax))
    for k in range(n_miss):  # 完全落空的查询
        o, d = random_miss_ray(rng)
        check(f"{name}/miss_ray#{k}", tree.query_ray(o, d), [])
        check(f"{name}/miss_ray_nearest#{k}", tree.query_ray_nearest(o, d), None)
        qmin, qmax = miss_box(rng)
        check(f"{name}/miss_aabb#{k}", tree.query_aabb(qmin, qmax), [])


def scenario(name, tris, n_rays=20, n_boxes=20, n_miss=5, seed=7):
    print(f"[scenario] {name}: {len(tris)} triangles")
    tree = BVH(tris)
    tree.check_invariants()
    rng = random.Random(seed)
    compare_queries(name, tris, tree, rng, n_rays, n_boxes, n_miss)
    return tree


def scenario_update(name, tris, n_moves, seed=11):
    """随机移动一批三角形：增量更新后的 BVH 必须与暴力、全量重建一致。"""
    print(f"[scenario] {name}: update {n_moves}/{len(tris)} triangles")
    rng = random.Random(seed)
    tree = BVH(tris)
    moved_ids = rng.sample(range(len(tris)), n_moves)
    offset = (rng.uniform(-5, 5), rng.uniform(-5, 5), rng.uniform(-5, 5))
    updates = move_triangles(tris, moved_ids, offset)

    tree.update(updates)
    tree.check_invariants()

    new_tris = list(tris)
    for idx, tri in updates:
        new_tris[idx] = tri

    rebuilt = BVH(new_tris)  # 全量重建作为第三方参照
    qrng = random.Random(seed + 1)
    for k in range(15):
        o, d = random_ray(qrng)
        want = brute.ray_query(new_tris, o, d)
        check(f"{name}/upd_ray#{k}", tree.query_ray(o, d), want)
        check(f"{name}/upd_ray_vs_rebuild#{k}", tree.query_ray(o, d),
              rebuilt.query_ray(o, d))
        check(f"{name}/upd_nearest#{k}",
              tree.query_ray_nearest(o, d), brute.ray_nearest(new_tris, o, d))
    for k in range(15):
        qmin, qmax = random_box(qrng)
        want = brute.aabb_query(new_tris, qmin, qmax)
        check(f"{name}/upd_aabb#{k}", tree.query_aabb(qmin, qmax), want)
        check(f"{name}/upd_aabb_vs_rebuild#{k}",
              tree.query_aabb(qmin, qmax), rebuilt.query_aabb(qmin, qmax))


def main():
    # ---- 边界用例 ----
    scenario("empty", [], n_rays=5, n_boxes=5, n_miss=3)
    scenario("single", [((0, 0, 0), (1, 0, 0), (0, 1, 0))],
             n_rays=30, n_boxes=30, n_miss=5)
    scenario("two", [((0, 0, 0), (1, 0, 0), (0, 1, 0)),
                     ((5, 5, 5), (6, 5, 5), (5, 6, 5))],
             n_rays=30, n_boxes=30, n_miss=5)
    degenerate = [((0, 0, 0), (0, 0, 0), (0, 0, 0)),
                  ((1, 1, 1), (2, 2, 2), (3, 3, 3)),  # 共线，零面积
                  ((0, 0, 0), (1, 0, 0), (0, 1, 0))]
    scenario("degenerate", degenerate, n_rays=30, n_boxes=30, n_miss=5)

    # ---- 常规规模 ----
    scenario("random_10", random_scene(10, seed=1), n_rays=30, n_boxes=30)
    scenario("random_500", random_scene(500, seed=2), n_rays=30, n_boxes=30)
    scenario("random_5000", random_scene(5000, seed=3), n_rays=20, n_boxes=20)

    # ---- 大量重叠 ----
    scenario("overlap_2000", overlap_scene(2000), n_rays=20, n_boxes=20)

    # ---- 增量更新一致性 ----
    scenario_update("update_small", random_scene(500, seed=4), n_moves=20)
    scenario_update("update_half", random_scene(1000, seed=5), n_moves=500)
    scenario_update("update_overlap", overlap_scene(500), n_moves=100)

    # 单三角形场景下把它移走再移回
    tris = [((0, 0, 0), (1, 0, 0), (0, 1, 0))]
    tree = BVH(tris)
    tree.update([(0, ((10, 10, 10), (11, 10, 10), (10, 11, 10)))])
    tree.check_invariants()
    check("update_single/ray_moved",
          tree.query_ray((10.2, 10.2, 5.0), (0.0, 0.0, -1.0)),
          brute.ray_query(tree.triangles, (10.2, 10.2, 5.0), (0.0, 0.0, -1.0)))
    check("update_single/old_pos_empty",
          tree.query_ray((0.2, 0.2, 5.0), (0.0, 0.0, -1.0)), [])

    # 把所有三角形全部更新（等价于重建的极端增量路径）
    tris = random_scene(200, seed=6)
    tree = BVH(tris)
    updates = move_triangles(tris, list(range(200)), (3.0, -2.0, 1.0))
    tree.update(updates)
    tree.check_invariants()
    new_tris = [tri for _, tri in updates]
    o, d = random_ray(random.Random(99))
    check("update_all/ray", tree.query_ray(o, d), brute.ray_query(new_tris, o, d))
    qmin, qmax = random_box(random.Random(99))
    check("update_all/aabb", tree.query_aabb(qmin, qmax),
          brute.aabb_query(new_tris, qmin, qmax))

    print(f"\n{CHECKS} checks, {len(FAILURES)} failures")
    if FAILURES:
        raise SystemExit(1)
    print("ALL DIFF TESTS PASSED")


if __name__ == "__main__":
    main()
