"""几何基元：射线-三角形、射线-AABB、AABB-三角形相交测试。

纯标准库实现。所有查询路径（BVH 与全量暴力）共用这里的判定函数，
保证对拍时两边语义完全一致。
"""

EPS = 1e-9


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def ray_triangle(orig, dir, v0, v1, v2):
    """Möller–Trumbore 射线-三角形求交。

    命中返回参数 t（t >= 0，含起点落在三角形上的情况），未命中返回 None。
    方向向量不需要归一化，t 与 dir 同尺度。
    """
    e1 = sub(v1, v0)
    e2 = sub(v2, v0)
    p = cross(dir, e2)
    det = dot(e1, p)
    if -EPS < det < EPS:  # 射线与三角形平面平行（或三角形退化）
        return None
    inv = 1.0 / det
    s = sub(orig, v0)
    u = dot(s, p) * inv
    if u < 0.0 or u > 1.0:
        return None
    q = cross(s, e1)
    v = dot(dir, q) * inv
    if v < 0.0 or u + v > 1.0:
        return None
    t = dot(e2, q) * inv
    if t < 0.0:
        return None
    return t


def ray_aabb(orig, dir, bmin, bmax):
    """射线与 AABB 的 slab 测试，命中返回 True。"""
    tmin = 0.0
    tmax = float("inf")
    for i in range(3):
        o = orig[i]
        d = dir[i]
        lo = bmin[i] - EPS
        hi = bmax[i] + EPS
        if -EPS < d < EPS:  # 与该轴平行
            if o < lo or o > hi:
                return False
        else:
            inv = 1.0 / d
            t1 = (lo - o) * inv
            t2 = (hi - o) * inv
            if t1 > t2:
                t1, t2 = t2, t1
            if t1 > tmin:
                tmin = t1
            if t2 < tmax:
                tmax = t2
            if tmin > tmax:
                return False
    return True


def _axis_overlap(axis, verts, c, e):
    """SAT 单轴测试：三角形在 axis 上的投影区间与盒子投影区间是否重叠。"""
    p0 = dot(verts[0], axis)
    p1 = dot(verts[1], axis)
    p2 = dot(verts[2], axis)
    lo = min(p0, p1, p2)
    hi = max(p0, p1, p2)
    r = e[0] * abs(axis[0]) + e[1] * abs(axis[1]) + e[2] * abs(axis[2])
    c_proj = dot(c, axis)
    return not (hi < c_proj - r - EPS or lo > c_proj + r + EPS)


_AXES = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def aabb_triangle_overlap(bmin, bmax, v0, v1, v2):
    """AABB 与三角形是否相交（分离轴定理，15 个候选轴）。"""
    c = (
        (bmin[0] + bmax[0]) * 0.5,
        (bmin[1] + bmax[1]) * 0.5,
        (bmin[2] + bmax[2]) * 0.5,
    )
    e = (
        (bmax[0] - bmin[0]) * 0.5,
        (bmax[1] - bmin[1]) * 0.5,
        (bmax[2] - bmin[2]) * 0.5,
    )
    verts = (v0, v1, v2)

    # 1) 盒子三个主轴
    for axis in _AXES:
        if not _axis_overlap(axis, verts, c, e):
            return False

    # 2) 三角形法线
    e1 = sub(v1, v0)
    e2 = sub(v2, v0)
    normal = cross(e1, e2)
    if not _axis_overlap(normal, verts, c, e):
        return False

    # 3) 三角形三条边与盒子主轴的叉积（9 个轴）
    e3 = sub(v2, v1)
    for edge in (e1, e2, e3):
        for axis in _AXES:
            test = cross(edge, axis)
            if not _axis_overlap(test, verts, c, e):
                return False
    return True


def aabb_of_triangle(v0, v1, v2):
    bmin = [min(v0[0], v1[0], v2[0]), min(v0[1], v1[1], v2[1]), min(v0[2], v1[2], v2[2])]
    bmax = [max(v0[0], v1[0], v2[0]), max(v0[1], v1[1], v2[1]), max(v0[2], v1[2], v2[2])]
    for i in range(3):
        if bmax[i] - bmin[i] < EPS:  # 退化方向稍微膨胀，避免数值漏检
            bmin[i] -= EPS
            bmax[i] += EPS
    return (tuple(bmin), tuple(bmax))


def centroid(v0, v1, v2):
    return (
        (v0[0] + v1[0] + v2[0]) / 3.0,
        (v0[1] + v1[1] + v2[1]) / 3.0,
        (v0[2] + v1[2] + v2[2]) / 3.0,
    )


def surface_area(bmin, bmax):
    dx = bmax[0] - bmin[0]
    dy = bmax[1] - bmin[1]
    dz = bmax[2] - bmin[2]
    return 2.0 * (dx * dy + dy * dz + dz * dx)
