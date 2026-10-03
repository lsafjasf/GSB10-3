"""耗时基准：建树 vs 全量查询，增量更新 vs 整体重建。

运行：python3 -m spatial_query.benchmark [三角形数] [射线数] [包围盒数] [移动次数]
默认：python3 -m spatial_query.benchmark            → 20000 三角形
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random
import time

from spatial_query.bvh import BVH
from spatial_query import brute_force
from spatial_query.scenes import make_triangles, make_rays, make_boxes, jitter_move


def timed(fn, repeat=1):
    """返回 (总秒数, 结果)，repeat>1 时取最佳一轮（结果来自最后一轮）。"""
    best = float("inf")
    result = None
    for _ in range(repeat):
        t0 = time.perf_counter()
        result = fn()
        dt = time.perf_counter() - t0
        best = min(best, dt)
    return best, result


def fmt(seconds):
    if seconds < 1e-3:
        return f"{seconds * 1e6:9.1f} us"
    if seconds < 1.0:
        return f"{seconds * 1e3:9.2f} ms"
    return f"{seconds:9.3f} s"


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    n_rays = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
    n_boxes = int(sys.argv[3]) if len(sys.argv) > 3 else 2000
    n_moves = int(sys.argv[4]) if len(sys.argv) > 4 else 2000

    rng = random.Random(42)
    tris = make_triangles(n, rng)
    rays = make_rays(n_rays, rng)
    boxes = make_boxes(n_boxes, rng)

    print(f"scene: {n} triangles, {n_rays} rays, {n_boxes} boxes, {n_moves} moves")
    print(f"python: {sys.version.split()[0]}\n")

    # ---- 建树 ----------------------------------------------------------------
    build_t, bvh = timed(lambda: BVH(tris), repeat=3)
    st = bvh.stats()
    print(f"[build] BVH: {fmt(build_t)}  (nodes={st['nodes']}, leaves={st['leaves']}, "
          f"max_leaf={st['max_leaf_tris']}, max_depth={st['max_depth']})")
    print(f"[build] brute force: 0 (无索引)\n")

    # ---- 射线查询 -------------------------------------------------------------
    bvh_ray_t, bvh_ray_res = timed(lambda: [bvh.ray_query(o, d) for o, d in rays], repeat=3)
    bf_ray_t, bf_ray_res = timed(lambda: [brute_force.ray_query(tris, o, d) for o, d in rays])
    assert bvh_ray_res == bf_ray_res, "ray differential check failed during benchmark"
    print(f"[ray query x{n_rays}]")
    print(f"  BVH:         {fmt(bvh_ray_t)}  ({bvh_ray_t / n_rays * 1e6:8.1f} us/query)")
    print(f"  brute force: {fmt(bf_ray_t)}  ({bf_ray_t / n_rays * 1e6:8.1f} us/query)")
    print(f"  speedup:     {bf_ray_t / bvh_ray_t:8.1f}x   (结果逐条一致)\n")

    # ---- 包围盒查询 -----------------------------------------------------------
    bvh_box_t, bvh_box_res = timed(lambda: [bvh.box_query(a, b) for a, b in boxes], repeat=3)
    bf_box_t, bf_box_res = timed(lambda: [brute_force.box_query(tris, a, b) for a, b in boxes])
    assert bvh_box_res == bf_box_res, "box differential check failed during benchmark"
    print(f"[box query x{n_boxes}]")
    print(f"  BVH:         {fmt(bvh_box_t)}  ({bvh_box_t / n_boxes * 1e6:8.1f} us/query)")
    print(f"  brute force: {fmt(bf_box_t)}  ({bf_box_t / n_boxes * 1e6:8.1f} us/query)")
    print(f"  speedup:     {bf_box_t / bvh_box_t:8.1f}x   (结果逐条一致)\n")

    # ---- 增量更新 vs 整体重建 ---------------------------------------------------
    moves = [(rng.randrange(n), None) for _ in range(n_moves)]
    moves = [(slot, jitter_move(bvh.triangles[slot][1], rng)) for slot, _ in moves]

    def do_updates():
        for slot, verts in moves:
            bvh.move_triangle(slot, verts)

    upd_t, _ = timed(do_updates, repeat=3)
    print(f"[incremental update x{n_moves}]")
    print(f"  refit (增量): {fmt(upd_t)}  ({upd_t / n_moves * 1e6:8.2f} us/move)")

    rebuild_t, _ = timed(lambda: bvh.rebuild(), repeat=3)
    print(f"  rebuild(整体): {fmt(rebuild_t)}  (单次全量重建)")
    print(f"  => {n_moves} 次移动: 增量 {fmt(upd_t)} vs 每次重建 "
          f"{fmt(rebuild_t * n_moves)}，快 {rebuild_t * n_moves / max(upd_t, 1e-12):.0f}x\n")

    # 更新后再做一次对拍，确认索引在移动后仍然正确
    sample_rays = rays[:200]
    sample_boxes = boxes[:200]
    ok_ray = all(bvh.ray_query(o, d) == brute_force.ray_query(bvh.triangles, o, d)
                 for o, d in sample_rays)
    ok_box = all(bvh.box_query(a, b) == brute_force.box_query(bvh.triangles, a, b)
                 for a, b in sample_boxes)
    print(f"[post-update differential] ray={'OK' if ok_ray else 'FAIL'} "
          f"box={'OK' if ok_box else 'FAIL'} (x{len(sample_rays)}/{len(sample_boxes)})")
    if not (ok_ray and ok_box):
        sys.exit(1)


if __name__ == "__main__":
    main()
