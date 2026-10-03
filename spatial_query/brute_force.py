"""全量暴力检测（参考实现）。

与 BVH 共用 geometry 中的同一套判定函数，用于对拍验证 BVH 查询结果。
"""

from .geometry import ray_triangle, aabb_triangle_overlap


def ray_query(triangles, orig, dir):
    """返回所有命中 (id, t)，按 t 升序。"""
    hits = []
    for tid, verts in triangles:
        t = ray_triangle(orig, dir, *verts)
        if t is not None:
            hits.append((tid, t))
    hits.sort(key=lambda h: (h[1], h[0]))
    return hits


def box_query(triangles, bmin, bmax):
    """返回与 AABB 相交的三角形 id 集合。"""
    result = set()
    for tid, verts in triangles:
        if aabb_triangle_overlap(bmin, bmax, *verts):
            result.add(tid)
    return result
