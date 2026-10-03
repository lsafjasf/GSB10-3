"""Test-mesh generators shared by the demo and the unit tests."""

from __future__ import annotations

import math
from typing import List, Tuple

Vec3 = Tuple[float, float, float]
Face = Tuple[int, int, int]


def plane_grid(n: int = 8, size: float = 1.0) -> Tuple[List[Vec3], List[Face]]:
    """Flat (n+1)x(n+1) grid in the z=0 plane."""
    verts: List[Vec3] = []
    for r in range(n + 1):
        for c in range(n + 1):
            verts.append((size * c / n, size * r / n, 0.0))
    faces: List[Face] = []

    def vid(r: int, c: int) -> int:
        return r * (n + 1) + c

    for r in range(n):
        for c in range(n):
            a, b = vid(r, c), vid(r, c + 1)
            d, e = vid(r + 1, c), vid(r + 1, c + 1)
            faces.append((a, b, d))
            faces.append((b, e, d))
    return verts, faces


def icosphere(subdivisions: int = 2) -> Tuple[List[Vec3], List[Face]]:
    """Geodesic sphere from a subdivided icosahedron (radius 1)."""
    t = (1.0 + math.sqrt(5.0)) / 2.0
    verts: List[Vec3] = [
        (-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0),
        (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t),
        (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1),
    ]
    faces: List[Face] = [
        (0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
        (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
        (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
        (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1),
    ]

    def normalize(v: Vec3) -> Vec3:
        l = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
        return (v[0] / l, v[1] / l, v[2] / l)

    verts = [normalize(v) for v in verts]

    for _ in range(subdivisions):
        midpoint_cache = {}

        def midpoint(a: int, b: int) -> int:
            key = (min(a, b), max(a, b))
            if key in midpoint_cache:
                return midpoint_cache[key]
            pa, pb = verts[a], verts[b]
            mid = normalize((
                (pa[0] + pb[0]) / 2,
                (pa[1] + pb[1]) / 2,
                (pa[2] + pb[2]) / 2,
            ))
            verts.append(mid)
            midpoint_cache[key] = len(verts) - 1
            return midpoint_cache[key]

        new_faces: List[Face] = []
        for a, b, c in faces:
            ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
            new_faces += [(a, ab, ca), (b, bc, ab), (c, ca, bc),
                          (ab, bc, ca)]
        faces = new_faces
    return verts, faces


def uv_sphere(segments: int = 24, rings: int = 12
              ) -> Tuple[List[Vec3], List[Face]]:
    """Latitude/longitude sphere; sliver triangles cluster at the poles."""
    verts: List[Vec3] = [(0.0, 0.0, 1.0)]  # north pole
    for r in range(1, rings):
        theta = math.pi * r / rings
        for s in range(segments):
            phi = 2.0 * math.pi * s / segments
            verts.append((
                math.sin(theta) * math.cos(phi),
                math.sin(theta) * math.sin(phi),
                math.cos(theta),
            ))
    verts.append((0.0, 0.0, -1.0))  # south pole
    south = len(verts) - 1

    faces: List[Face] = []

    def vid(r: int, s: int) -> int:
        return 1 + (r - 1) * segments + (s % segments)

    for s in range(segments):
        faces.append((0, vid(1, s + 1), vid(1, s)))
    for r in range(1, rings - 1):
        for s in range(segments):
            a, b = vid(r, s), vid(r, s + 1)
            c, d = vid(r + 1, s), vid(r + 1, s + 1)
            faces.append((a, d, b))
            faces.append((a, c, d))
    for s in range(segments):
        faces.append((south, vid(rings - 1, s), vid(rings - 1, s + 1)))
    return verts, faces


def tent_mesh() -> Tuple[List[Vec3], List[Face]]:
    """One raised vertex fanned to a ring with a single far vertex.

    Every ring vertex sits at radius 1 except one at radius 8, so the two
    faces touching that far vertex have ~8x the area of their neighbours
    while subtending small angles at the apex.  Area weighting is dragged
    toward those big faces; angle weighting is not.  This asymmetry is
    what makes the two schemes produce visibly different apex normals.
    """
    verts: List[Vec3] = [(0.0, 0.0, 1.0)]  # apex
    m = 12
    for s in range(m):
        radius = 8.0 if s == 0 else 1.0
        phi = 2.0 * math.pi * s / m
        verts.append((radius * math.cos(phi), radius * math.sin(phi), 0.0))
    faces: List[Face] = [(0, 1 + s, 1 + (s + 1) % m) for s in range(m)]
    return verts, faces


def mesh_with_degenerate_and_isolated(
    ) -> Tuple[List[Vec3], List[Face], int, Tuple[int, ...]]:
    """Plane grid polluted with degenerate faces + one isolated vertex.

    Returns (vertices, faces, expected_degenerate_count, isolated_ids).
    ``isolated_ids`` covers the vertex referenced by no face at all plus
    the duplicate-coordinate vertex whose only face gets culled.
    """
    verts, faces = plane_grid(4)
    # 1) repeated index inside the face
    faces.append((0, 0, 1))
    # 2) three collinear points on the grid's bottom edge
    faces.append((0, 1, 2))
    # 3) two distinct indices sharing identical coordinates; the extra
    #    vertex is referenced only by this (culled) face, so it ends up
    #    isolated as well
    dup = len(verts)
    verts.append(verts[3])
    faces.append((3, dup, 4))
    # 4) an isolated vertex, referenced by no face at all
    verts.append((10.0, 10.0, 10.0))
    return verts, faces, 3, (dup, len(verts) - 1)
