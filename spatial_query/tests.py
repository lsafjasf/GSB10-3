"""对拍自测：BVH 查询结果必须与全量暴力检测完全一致。

运行：python3 -m spatial_query.tests   （或 python3 spatial_query/tests.py）
覆盖：空网格、单三角形、随机场景、大量重叠、退化三角形、查询完全落空、
      增量更新（单个/批量移动、旋转）、更新后重建。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random

from spatial_query.bvh import BVH
from spatial_query import brute_force
from spatial_query.scenes import (
    make_triangles,
    make_heavy_overlap,
    make_degenerate,
    make_rays,
    make_boxes,
    jitter_move,
    rotate_move,
)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, cond, detail))
    if not cond:
        print(f"  [FAIL] {name} {detail}")


def compare_all(tag, bvh, triangles, rays, boxes):
    """对拍核心：逐条射线 / 逐个包围盒与暴力结果比对。"""
    ray_bad = 0
    for orig, dir in rays:
        got = bvh.ray_query(orig, dir)
        want = brute_force.ray_query(triangles, orig, dir)
        if got != want:
            ray_bad += 1
            if ray_bad <= 3:
                print(f"    ray mismatch: got={got[:5]} want={want[:5]}")
    check(f"{tag}: ray x{len(rays)}", ray_bad == 0, f"mismatch={ray_bad}")

    box_bad = 0
    for bmin, bmax in boxes:
        got = bvh.box_query(bmin, bmax)
        want = brute_force.box_query(triangles, bmin, bmax)
        if got != want:
            box_bad += 1
            if box_bad <= 3:
                print(f"    box mismatch: got-want={sorted(got - want)[:5]} want-got={sorted(want - got)[:5]}")
    check(f"{tag}: box x{len(boxes)}", box_bad == 0, f"mismatch={box_bad}")


def test_empty():
    rng = random.Random(1)
    bvh = BVH([])
    rays = make_rays(50, rng)
    boxes = make_boxes(50, rng)
    check("empty: no nodes", bvh.nodes == [])
    check("empty: ray", all(bvh.ray_query(o, d) == [] for o, d in rays))
    check("empty: box", all(bvh.box_query(a, b) == set() for a, b in boxes))
    check("empty: nearest", bvh.nearest_hit((0, 0, 0), (0, 0, 1)) is None)


def test_single():
    rng = random.Random(2)
    tri = [(7, ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)))]
    bvh = BVH(tri)
    rays = make_rays(200, rng, world=1.0)
    boxes = make_boxes(200, rng, world=1.0)
    compare_all("single", bvh, tri, rays, boxes)
    check("single: direct hit", bvh.ray_query((0.25, 0.25, 1.0), (0, 0, -1)) == [(7, 1.0)])
    check("single: miss", bvh.ray_query((5.0, 5.0, 1.0), (0, 0, -1)) == [])
    check("single: box contains", bvh.box_query((-1, -1, -1), (2, 2, 2)) == {7})
    check("single: box disjoint", bvh.box_query((5, 5, 5), (6, 6, 6)) == set())


def test_random_scene():
    rng = random.Random(3)
    tris = make_triangles(3000, rng)
    bvh = BVH(tris)
    rays = make_rays(500, rng)
    boxes = make_boxes(500, rng)
    compare_all("random(3000)", bvh, tris, rays, boxes)


def test_heavy_overlap():
    rng = random.Random(4)
    tris = make_heavy_overlap(2000, rng)
    bvh = BVH(tris)
    # 垂直穿过重叠面的射线：应命中全部 2000 个三角形
    hit_ray = ((1.0 / 3, 1.0 / 3, 5.0), (0.0, 0.0, -1.0))
    got = bvh.ray_query(*hit_ray)
    want = brute_force.ray_query(tris, *hit_ray)
    check("overlap: ray hits all", len(got) == len(want) == 2000, f"got={len(got)} want={len(want)}")
    check("overlap: ray order", got == want)
    # 重叠面附近的盒子：应圈出全部
    got_box = bvh.box_query((-1, -1, 0.4), (3, 3, 0.6))
    want_box = brute_force.box_query(tris, (-1, -1, 0.4), (3, 3, 0.6))
    check("overlap: box hits all", got_box == want_box == set(range(2000)))
    # 随机对拍
    rays = make_rays(200, rng, world=2.0)
    boxes = make_boxes(200, rng, world=2.0)
    compare_all("overlap(2000)", bvh, tris, rays, boxes)


def test_degenerate():
    rng = random.Random(5)
    tris = make_degenerate(2000, rng)
    bvh = BVH(tris)
    rays = make_rays(300, rng)
    boxes = make_boxes(300, rng)
    compare_all("degenerate(2000)", bvh, tris, rays, boxes)


def test_total_miss():
    rng = random.Random(6)
    tris = make_triangles(2000, rng)
    bvh = BVH(tris)
    rays = make_rays(200, rng, miss_ratio=1.0)
    boxes = make_boxes(200, rng, miss_ratio=1.0)
    check("miss: ray all empty", all(bvh.ray_query(o, d) == [] for o, d in rays))
    check("miss: box all empty", all(bvh.box_query(a, b) == set() for a, b in boxes))
    # 起点在场景上方、方向继续远离场景（必然落空）
    back = [((5, 5, 20), (0, 0, 1)), ((-5, 5, 5), (-1, 0, 0))]
    check("miss: backward ray", all(bvh.ray_query(o, d) == [] for o, d in back))


def test_incremental_update():
    rng = random.Random(7)
    tris = make_triangles(3000, rng)
    bvh = BVH(tris)
    rays = make_rays(300, rng)
    boxes = make_boxes(300, rng)

    # 单个移动
    bvh.move_triangle(123, jitter_move(tris[123][1], rng))
    compare_all("update: single move", bvh, bvh.triangles, rays, boxes)

    # 批量移动 5%
    moved = rng.sample(range(len(tris)), 150)
    for slot in moved:
        bvh.move_triangle(slot, jitter_move(bvh.triangles[slot][1], rng))
    compare_all("update: batch 150", bvh, bvh.triangles, rays, boxes)

    # 旋转移动
    for slot in rng.sample(range(len(tris)), 50):
        bvh.move_triangle(slot, rotate_move(bvh.triangles[slot][1], 0.7, (5, 5, 5)))
    compare_all("update: rotate 50", bvh, bvh.triangles, rays, boxes)

    # 移动到远处（包围盒需要大幅扩张）
    for slot in rng.sample(range(len(tris)), 20):
        v = bvh.triangles[slot][1]
        bvh.move_triangle(slot, tuple((x + 100, y + 100, z + 100) for x, y, z in v))
    compare_all("update: teleport 20", bvh, bvh.triangles, rays, boxes)

    # 更新后整体重建，结果仍须一致
    bvh.rebuild()
    compare_all("update: after rebuild", bvh, bvh.triangles, rays, boxes)


def test_boundary_touch():
    """查询盒/射线恰好贴着三角形边界的数值边界情形。"""
    tri = [(0, ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)))]
    bvh = BVH(tri)
    # 起点在三角形平面上
    check("touch: origin on plane", bvh.ray_query((0.25, 0.25, 0.0), (0, 0, -1)) ==
          brute_force.ray_query(tri, (0.25, 0.25, 0.0), (0, 0, -1)))
    # 盒子与三角形平面共面接触
    got = bvh.box_query((-0.5, -0.5, 0.0), (0.5, 0.5, 0.0))
    want = brute_force.box_query(tri, (-0.5, -0.5, 0.0), (0.5, 0.5, 0.0))
    check("touch: coplanar box", got == want == {0})
    # 射线擦边（穿过顶点 (1,0,0)）
    got = bvh.ray_query((1.0, 0.0, 1.0), (0, 0, -1))
    want = brute_force.ray_query(tri, (1.0, 0.0, 1.0), (0, 0, -1))
    check("touch: edge-grazing ray", got == want)


def main():
    tests = [
        test_empty,
        test_single,
        test_random_scene,
        test_heavy_overlap,
        test_degenerate,
        test_total_miss,
        test_incremental_update,
        test_boundary_touch,
    ]
    for fn in tests:
        print(f"== {fn.__name__}")
        fn()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        sys.exit(1)
    print("ALL DIFFERENTIAL TESTS PASSED")


if __name__ == "__main__":
    main()
