"""全量（暴力）参考实现，与 BVH 共用同一套几何判定。"""
from geometry import ray_triangle, tri_box_overlap


def aabb_query(triangles, qmin, qmax):
    """O(n) 遍历所有三角形做 SAT 精确相交。"""
    return [
        i for i, tri in enumerate(triangles)
        if tri_box_overlap(tri, qmin, qmax)
    ]


def ray_query(triangles, origin, direction):
    """O(n) 遍历所有三角形做 Moller-Trumbore，结果按 t 升序。"""
    hits = []
    for i, (v0, v1, v2) in enumerate(triangles):
        t = ray_triangle(origin, direction, v0, v1, v2)
        if t is not None:
            hits.append((t, i))
    hits.sort(key=lambda h: (h[0], h[1]))
    return hits


def ray_nearest(triangles, origin, direction):
    hits = ray_query(triangles, origin, direction)
    return hits[0] if hits else None
