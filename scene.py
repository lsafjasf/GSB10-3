"""随机场景/查询生成器（固定种子，结果可复现）。"""
import math
import random


def random_triangle(rng, center, size):
    verts = []
    for _ in range(3):
        verts.append(tuple(
            center[a] + rng.uniform(-size, size) for a in range(3)
        ))
    return tuple(verts)


def random_scene(n, seed=0, clusters=16, world=50.0, size=0.8):
    """三角形按簇聚集，模拟“场景里很多物体”。"""
    rng = random.Random(seed)
    centers = [
        (rng.uniform(-world, world), rng.uniform(-world, world),
         rng.uniform(-world, world))
        for _ in range(clusters)
    ]
    tris = []
    for _ in range(n):
        center = centers[rng.randrange(clusters)]
        tris.append(random_triangle(rng, center, size))
    return tris


def overlap_scene(n, seed=1):
    """大量重叠：所有三角形挤在同一小区域，且包含若干完全相同的三角形。"""
    rng = random.Random(seed)
    shared = (
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
    )
    tris = []
    for i in range(n):
        if i % 3 == 0:
            tris.append(shared)
        else:
            tris.append(random_triangle(rng, (0.5, 0.5, 0.0), 0.05))
    return tris


def move_triangles(triangles, ids, offset):
    """平移指定三角形，返回 (索引, 新三角形) 列表，不改动原列表。"""
    dx, dy, dz = offset
    return [
        (i, tuple(tuple((v[0] + dx, v[1] + dy, v[2] + dz)) for v in triangles[i]))
        for i in ids
    ]


def normalize(d):
    n = math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2])
    return (d[0] / n, d[1] / n, d[2] / n)


def random_ray(rng, origin_box=(-120.0, -100.0), direction=(0.6, 1.6)):
    origin = tuple(rng.uniform(-origin_box[0], origin_box[0]) for _ in range(3))
    d = normalize(tuple(rng.uniform(-1.0, 1.0) for _ in range(3)))
    return origin, d


def random_miss_ray(rng):
    """原点远离场景且朝远离原点方向的射线，保证落空。"""
    axis = rng.randrange(3)
    origin = [rng.uniform(-1.0, 1.0) for _ in range(3)]
    origin[axis] = 200.0
    d = [0.0, 0.0, 0.0]
    d[axis] = 1.0
    return tuple(origin), tuple(d)


def random_box(rng, center=20.0, half=1.5):
    c = tuple(rng.uniform(-center, center) for _ in range(3))
    h = tuple(rng.uniform(0.2, half) for _ in range(3))
    qmin = tuple(c[a] - h[a] for a in range(3))
    qmax = tuple(c[a] + h[a] for a in range(3))
    return qmin, qmax


def miss_box(rng):
    c = 200.0 + rng.uniform(0.0, 50.0)
    return (c, c, c), (c + 1.0, c + 1.0, c + 1.0)


def aimed_ray(rng, triangles, origin_dist=30.0):
    """瞄准随机三角形质心的射线，保证大概率命中。"""
    tri = triangles[rng.randrange(len(triangles))]
    cx = sum(v[0] for v in tri) / 3.0
    cy = sum(v[1] for v in tri) / 3.0
    cz = sum(v[2] for v in tri) / 3.0
    d = normalize((rng.uniform(-0.05, 0.05), rng.uniform(-0.05, 0.05), -1.0))
    origin = (cx - d[0] * origin_dist, cy - d[1] * origin_dist, cz - d[2] * origin_dist)
    return origin, d


def aimed_box(rng, triangles, half=0.6):
    """以随机三角形质心为中心的查询盒，大概率与三角形相交。"""
    tri = triangles[rng.randrange(len(triangles))]
    c = tuple(sum(v[a] for v in tri) / 3.0 for a in range(3))
    qmin = tuple(c[a] - half for a in range(3))
    qmax = tuple(c[a] + half for a in range(3))
    return qmin, qmax
