"""测试场景与查询生成器（固定随机种子，保证可复现）。"""

import math
import random


def make_triangles(n, rng, world=10.0):
    """在单位尺度 world 立方体内随机撒 n 个三角形。"""
    tris = []
    for i in range(n):
        cx = rng.uniform(0, world)
        cy = rng.uniform(0, world)
        cz = rng.uniform(0, world)
        r = rng.uniform(0.05, 0.5)
        v0 = (cx + rng.uniform(-r, r), cy + rng.uniform(-r, r), cz + rng.uniform(-r, r))
        v1 = (cx + rng.uniform(-r, r), cy + rng.uniform(-r, r), cz + rng.uniform(-r, r))
        v2 = (cx + rng.uniform(-r, r), cy + rng.uniform(-r, r), cz + rng.uniform(-r, r))
        tris.append((i, (v0, v1, v2)))
    return tris


def make_heavy_overlap(n, rng):
    """大量重叠：所有三角形共享同一底面（z=0.5 平面），顶点仅微小抖动。"""
    base = ((0.0, 0.0, 0.5), (2.0, 0.0, 0.5), (0.0, 2.0, 0.5))
    tris = []
    for i in range(n):
        verts = tuple(
            (vx + rng.uniform(-5e-4, 5e-4), vy + rng.uniform(-5e-4, 5e-4), vz)
            for (vx, vy, vz) in base
        )
        tris.append((i, verts))
    return tris


def make_degenerate(n, rng, world=10.0):
    """一半正常三角形，一半退化（三点共线 / 两顶点重合）。"""
    tris = []
    for i in range(n):
        if i % 2 == 0:
            cx, cy, cz = rng.uniform(0, world), rng.uniform(0, world), rng.uniform(0, world)
            r = rng.uniform(0.05, 0.5)
            verts = tuple(
                (cx + rng.uniform(-r, r), cy + rng.uniform(-r, r), cz + rng.uniform(-r, r))
                for _ in range(3)
            )
        else:
            ax, ay, az = rng.uniform(0, world), rng.uniform(0, world), rng.uniform(0, world)
            dx, dy, dz = rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1)
            t1, t2 = rng.uniform(0, 1), rng.uniform(0, 1)
            v0 = (ax, ay, az)
            v1 = (ax + dx * t1, ay + dy * t1, az + dz * t1)
            v2 = (ax + dx * t2, ay + dy * t2, az + dz * t2)
            verts = (v0, v1, v2)
        tris.append((i, verts))
    return tris


def make_rays(m, rng, world=10.0, miss_ratio=0.3):
    """随机射线：miss_ratio 比例指向场景外（保证落空）。"""
    rays = []
    for _ in range(m):
        if rng.random() < miss_ratio:
            orig = (rng.uniform(0, world), rng.uniform(0, world), world + rng.uniform(1, 5))
            dir = (rng.uniform(-0.2, 0.2), rng.uniform(-0.2, 0.2), rng.uniform(0.5, 1.0))
        else:
            orig = (rng.uniform(0, world), rng.uniform(0, world), rng.uniform(0, world))
            dir = (rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1))
        rays.append((orig, dir))
    return rays


def make_boxes(m, rng, world=10.0, miss_ratio=0.3):
    """随机 AABB：miss_ratio 比例完全落在场景外。"""
    boxes = []
    for _ in range(m):
        if rng.random() < miss_ratio:
            cx = rng.uniform(world + 2, world + 5)
            cy = rng.uniform(world + 2, world + 5)
            cz = rng.uniform(world + 2, world + 5)
        else:
            cx = rng.uniform(0, world)
            cy = rng.uniform(0, world)
            cz = rng.uniform(0, world)
        hx = rng.uniform(0.05, 1.0)
        hy = rng.uniform(0.05, 1.0)
        hz = rng.uniform(0.05, 1.0)
        boxes.append(((cx - hx, cy - hy, cz - hz), (cx + hx, cy + hy, cz + hz)))
    return boxes


def jitter_move(verts, rng, max_step=0.5):
    """给三角形顶点加随机位移，模拟物体移动。"""
    dx = rng.uniform(-max_step, max_step)
    dy = rng.uniform(-max_step, max_step)
    dz = rng.uniform(-max_step, max_step)
    return tuple((vx + dx, vy + dy, vz + dz) for (vx, vy, vz) in verts)


def rotate_move(verts, angle, axis_point=(0.0, 0.0, 0.0)):
    """绕 z 轴旋转（标准库即可，验证旋转类移动）。"""
    c = math.cos(angle)
    s = math.sin(angle)
    ax, ay, az = axis_point
    out = []
    for (vx, vy, vz) in verts:
        x = vx - ax
        y = vy - ay
        out.append((ax + x * c - y * s, ay + x * s + y * c, vz))
    return tuple(out)
