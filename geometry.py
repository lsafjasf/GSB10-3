"""基础几何原语（仅标准库）。

- 射线-三角形：Moller-Trumbore，双面，命中返回非负距离 t
- 三角形-AABB：13 轴 SAT 精确重叠
- 射线-AABB：slab 法，方向分量允许为 0

BVH 与暴力对拍均调用本文件中的同一组函数，保证判定口径一致。
"""
import math

EPS = 1e-12
_INF = math.inf


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def tri_bounds(tri):
    v0, v1, v2 = tri
    bmin = (
        min(v0[0], v1[0], v2[0]),
        min(v0[1], v1[1], v2[1]),
        min(v0[2], v1[2], v2[2]),
    )
    bmax = (
        max(v0[0], v1[0], v2[0]),
        max(v0[1], v1[1], v2[1]),
        max(v0[2], v1[2], v2[2]),
    )
    return bmin, bmax


def union_bounds(a_min, a_max, b_min, b_max):
    return (
        (min(a_min[0], b_min[0]), min(a_min[1], b_min[1]), min(a_min[2], b_min[2])),
        (max(a_max[0], b_max[0]), max(a_max[1], b_max[1]), max(a_max[2], b_max[2])),
    )


def boxes_overlap(a_min, a_max, b_min, b_max):
    return (
        a_min[0] <= b_max[0] and a_max[0] >= b_min[0]
        and a_min[1] <= b_max[1] and a_max[1] >= b_min[1]
        and a_min[2] <= b_max[2] and a_max[2] >= b_min[2]
    )


def surface_area(bmin, bmax):
    dx = bmax[0] - bmin[0]
    dy = bmax[1] - bmin[1]
    dz = bmax[2] - bmin[2]
    return 2.0 * (dx * dy + dy * dz + dz * dx)


def ray_triangle(origin, direction, v0, v1, v2):
    """Moller-Trumbore。命中返回 t（t>=0），未命中返回 None。"""
    e1 = sub(v1, v0)
    e2 = sub(v2, v0)
    pvec = cross(direction, e2)
    det = dot(e1, pvec)
    if -EPS < det < EPS:
        return None
    inv_det = 1.0 / det
    tvec = sub(origin, v0)
    u = dot(tvec, pvec) * inv_det
    if u < 0.0 or u > 1.0:
        return None
    qvec = cross(tvec, e1)
    v = dot(direction, qvec) * inv_det
    if v < 0.0 or u + v > 1.0:
        return None
    t = dot(e2, qvec) * inv_det
    if t < -EPS:
        return None
    if t < 0.0:
        t = 0.0
    return t


def _sat_separated(t0, t1, t2, axis, hx, hy, hz):
    p0 = dot(t0, axis)
    p1 = dot(t1, axis)
    p2 = dot(t2, axis)
    lo = min(p0, p1, p2)
    hi = max(p0, p1, p2)
    r = hx * abs(axis[0]) + hy * abs(axis[1]) + hz * abs(axis[2])
    return lo > r or hi < -r


def tri_box_overlap(tri, bmin, bmax):
    """三角形与轴对齐包围盒的精确相交（SAT：3 盒轴 + 面法向 + 9 叉积轴）。"""
    v0, v1, v2 = tri
    cx = (bmin[0] + bmax[0]) * 0.5
    cy = (bmin[1] + bmax[1]) * 0.5
    cz = (bmin[2] + bmax[2]) * 0.5
    hx = (bmax[0] - bmin[0]) * 0.5
    hy = (bmax[1] - bmin[1]) * 0.5
    hz = (bmax[2] - bmin[2]) * 0.5
    t0 = (v0[0] - cx, v0[1] - cy, v0[2] - cz)
    t1 = (v1[0] - cx, v1[1] - cy, v1[2] - cz)
    t2 = (v2[0] - cx, v2[1] - cy, v2[2] - cz)

    e0 = sub(t1, t0)
    e1 = sub(t2, t1)
    e2 = sub(t0, t2)

    # 1) 三角形面法向
    normal = cross(e0, sub(t2, t0))
    if normal != (0.0, 0.0, 0.0) and _sat_separated(t0, t1, t2, normal, hx, hy, hz):
        return False
    # 2) 三个盒轴
    for axis in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)):
        if _sat_separated(t0, t1, t2, axis, hx, hy, hz):
            return False
    # 3) 三角形三条边分别与三个盒轴叉积
    for edge in (e0, e1, e2):
        ex, ey, ez = edge
        for axis in (
            (0.0, -ez, ey),
            (ez, 0.0, -ex),
            (-ey, ex, 0.0),
        ):
            if _sat_separated(t0, t1, t2, axis, hx, hy, hz):
                return False
    return True


def ray_box(origin, direction, bmin, bmax, t_max=_INF):
    """slab 法。射线与盒相交返回近交点距离（可为 0），否则 None。"""
    tmin = -_INF
    tmax = _INF
    ox, oy, oz = origin
    dx, dy, dz = direction

    if -EPS < dx < EPS:
        if ox < bmin[0] or ox > bmax[0]:
            return None
    else:
        t1 = (bmin[0] - ox) / dx
        t2 = (bmax[0] - ox) / dx
        if t1 > t2:
            t1, t2 = t2, t1
        tmin = max(tmin, t1)
        tmax = min(tmax, t2)
        if tmin > tmax:
            return None

    if -EPS < dy < EPS:
        if oy < bmin[1] or oy > bmax[1]:
            return None
    else:
        t1 = (bmin[1] - oy) / dy
        t2 = (bmax[1] - oy) / dy
        if t1 > t2:
            t1, t2 = t2, t1
        tmin = max(tmin, t1)
        tmax = min(tmax, t2)
        if tmin > tmax:
            return None

    if -EPS < dz < EPS:
        if oz < bmin[2] or oz > bmax[2]:
            return None
    else:
        t1 = (bmin[2] - oz) / dz
        t2 = (bmax[2] - oz) / dz
        if t1 > t2:
            t1, t2 = t2, t1
        tmin = max(tmin, t1)
        tmax = min(tmax, t2)
        if tmin > tmax:
            return None

    if tmax < -EPS:
        return None
    if tmin < 0.0:
        tmin = 0.0
    if tmin > t_max:
        return None
    return tmin
