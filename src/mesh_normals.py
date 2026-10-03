"""Triangle-mesh vertex-normal reconstruction (standard library only).

Two weighting schemes are supported:

* ``"area"``  - each incident face contributes its (unnormalized) cross
  product, i.e. weight = 2 * face area.  This is the classic
  ``normal = normalize(sum(cross(b-a, c-a)))`` rule.
* ``"angle"`` - each incident face contributes its unit face normal
  multiplied by the interior angle the face subtends at that vertex
  (Thurmer/Wuthrich angle weighting).  Sliver triangles influence area
  weighting strongly (large area, tiny angle at the sharp vertex); angle
  weighting is insensitive to that and is the safer default on
  poorly-tessellated meshes.

Degenerate triangles are detected and culled *before* any accumulation:

* repeated vertex index inside a face (``i == j``), or
* zero / near-zero area measured as
  ``|e1 x e2| / (|e1| * |e2|) < degenerate_eps``  (collinear points;
  the ratio makes the test scale independent).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence, Tuple

Vec3 = Tuple[float, float, float]

AREA = "area"
ANGLE = "angle"
_WEIGHTINGS = (AREA, ANGLE)


@dataclass(frozen=True)
class RebuildResult:
    """Outcome of :func:`rebuild_vertex_normals`."""

    normals: List[Vec3]
    weighting: str
    valid_faces: int
    degenerate_faces: int
    isolated_vertices: Tuple[int, ...]

    @property
    def total_faces(self) -> int:
        return self.valid_faces + self.degenerate_faces

    def max_norm_error(self) -> float:
        """max | ||n|| - 1 | over every output normal (isolated included)."""
        return max(
            abs(math.sqrt(nx * nx + ny * ny + nz * nz) - 1.0)
            for nx, ny, nz in self.normals
        )

    def assert_unit_normals(self, eps: float = 1e-9) -> None:
        """Assert every rebuilt normal is a unit vector."""
        err = self.max_norm_error()
        assert err < eps, f"non-unit normal found, max |len-1|={err!r}"


def _sub(p: Vec3, q: Vec3) -> Vec3:
    return (p[0] - q[0], p[1] - q[1], p[2] - q[2])


def _cross(u: Vec3, v: Vec3) -> Vec3:
    return (
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    )


def _dot(u: Vec3, v: Vec3) -> float:
    return u[0] * v[0] + u[1] * v[1] + u[2] * v[2]


def _norm(u: Vec3) -> float:
    return math.sqrt(_dot(u, u))


def _is_degenerate(i: int, j: int, k: int,
                   verts: Sequence[Vec3], eps: float) -> bool:
    # Repeated point (duplicated index).
    if i == j or j == k or i == k:
        return True
    e1 = _sub(verts[j], verts[i])
    e2 = _sub(verts[k], verts[i])
    l1 = _norm(e1)
    l2 = _norm(e2)
    if l1 == 0.0 or l2 == 0.0:
        # Distinct indices pointing at identical coordinates.
        return True
    cr = _cross(e1, e2)
    # sin(angle between edges) < eps  -> zero-area / collinear triangle.
    return _norm(cr) < eps * l1 * l2


def rebuild_vertex_normals(
    vertices: Sequence[Vec3],
    faces: Sequence[Tuple[int, int, int]],
    weighting: str = AREA,
    degenerate_eps: float = 1e-12,
    isolated_normal: Vec3 = (0.0, 0.0, 1.0),
) -> RebuildResult:
    """Rebuild per-vertex normals from face normals.

    Parameters
    ----------
    vertices:
        ``[(x, y, z), ...]`` vertex positions.
    faces:
        ``[(i, j, k), ...]`` indexed triangle list.
    weighting:
        ``"area"`` or ``"angle"``.
    degenerate_eps:
        Collinearity threshold on ``|e1 x e2| / (|e1||e2|)``; triangles
        below it are culled as zero-area.
    isolated_normal:
        Unit vector assigned to vertices referenced by no valid face.
        It is normalized internally.

    Returns a :class:`RebuildResult` with unit normals for *every* vertex.
    """
    if weighting not in _WEIGHTINGS:
        raise ValueError(
            f"unknown weighting {weighting!r}, expected one of {_WEIGHTINGS}"
        )

    n = len(vertices)
    accum: List[Vec3] = [(0.0, 0.0, 0.0)] * n
    used = [False] * n
    valid = 0
    degenerate = 0

    for face in faces:
        if len(face) != 3:
            raise ValueError(f"only triangles are supported, got face {face!r}")
        i, j, k = face
        if not (0 <= i < n and 0 <= j < n and 0 <= k < n):
            raise ValueError(f"face {face!r} indexes outside 0..{n - 1}")

        if _is_degenerate(i, j, k, vertices, degenerate_eps):
            degenerate += 1
            continue

        p0, p1, p2 = vertices[i], vertices[j], vertices[k]
        e1 = _sub(p1, p0)
        e2 = _sub(p2, p0)
        cr = _cross(e1, e2)
        cr_len = _norm(cr)

        if weighting == AREA:
            # Unnormalized cross product == (2 * area) * unit-face-normal.
            wi, wj, wk = cr, cr, cr
        else:
            fnx, fny, fnz = cr[0] / cr_len, cr[1] / cr_len, cr[2] / cr_len
            # Interior angle at each corner via atan2(|cross|, dot).
            def corner(a: Vec3, b: Vec3, c: Vec3) -> float:
                u = _sub(b, a)
                v = _sub(c, a)
                return math.atan2(_norm(_cross(u, v)), _dot(u, v))

            ai = corner(p0, p1, p2)
            aj = corner(p1, p2, p0)
            ak = math.pi - ai - aj
            wi = (fnx * ai, fny * ai, fnz * ai)
            wj = (fnx * aj, fny * aj, fnz * aj)
            wk = (fnx * ak, fny * ak, fnz * ak)

        for idx, w in ((i, wi), (j, wj), (k, wk)):
            ax, ay, az = accum[idx]
            accum[idx] = (ax + w[0], ay + w[1], az + w[2])
            used[idx] = True
        valid += 1

    fx, fy, fz = isolated_normal
    fl = math.sqrt(fx * fx + fy * fy + fz * fz)
    if fl == 0.0:
        raise ValueError("isolated_normal must be a non-zero vector")
    fallback = (fx / fl, fy / fl, fz / fl)

    normals: List[Vec3] = []
    isolated: List[int] = []
    for idx in range(n):
        ax, ay, az = accum[idx]
        al = math.sqrt(ax * ax + ay * ay + az * az)
        if al == 0.0:
            # Vertex referenced by no valid face (isolated vertex).
            isolated.append(idx)
            normals.append(fallback)
        else:
            normals.append((ax / al, ay / al, az / al))

    return RebuildResult(
        normals=normals,
        weighting=weighting,
        valid_faces=valid,
        degenerate_faces=degenerate,
        isolated_vertices=tuple(isolated),
    )


def angular_difference(a: Vec3, b: Vec3) -> float:
    """Angle in degrees between two unit vectors (range 0..180)."""
    d = _dot(a, b)
    d = max(-1.0, min(1.0, d))
    return math.degrees(math.acos(d))


def compare_weightings(
    vertices: Sequence[Vec3],
    faces: Sequence[Tuple[int, int, int]],
    **kwargs,
) -> dict:
    """Run both weightings and return discrepancy statistics (degrees)."""
    ra = rebuild_vertex_normals(vertices, faces, AREA, **kwargs)
    rg = rebuild_vertex_normals(vertices, faces, ANGLE, **kwargs)
    assert ra.valid_faces == rg.valid_faces
    assert ra.degenerate_faces == rg.degenerate_faces

    diffs = [
        angular_difference(na, ng)
        for na, ng in zip(ra.normals, rg.normals)
    ]
    active = [d for v, d in enumerate(diffs) if v not in
              set(ra.isolated_vertices)]
    return {
        "valid_faces": ra.valid_faces,
        "degenerate_faces": ra.degenerate_faces,
        "isolated_vertices": list(ra.isolated_vertices),
        "max_angle_deg": max(diffs, default=0.0),
        "mean_angle_deg": sum(diffs) / len(diffs) if diffs else 0.0,
        "max_angle_active_deg": max(active, default=0.0),
        "mean_angle_active_deg": (
            sum(active) / len(active) if active else 0.0
        ),
        "vertices_over_1deg": sum(1 for d in diffs if d > 1.0),
        "vertices_over_5deg": sum(1 for d in diffs if d > 5.0),
        "area_result": ra,
        "angle_result": rg,
    }
