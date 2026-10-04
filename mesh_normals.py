"""Vertex normal reconstruction for triangle meshes.

The module uses only the Python standard library. It supports area-weighted
and angle-weighted accumulation, excludes degenerate triangles, and always
returns unit-length vertex normals. Vertices without any valid incident
triangle receive a deterministic unit fallback normal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

Vec3 = Tuple[float, float, float]
Face = Tuple[int, int, int]

_DEFAULT_FALLBACK: Vec3 = (0.0, 0.0, 1.0)
_DEFAULT_EPS = 1.0e-15


@dataclass(frozen=True)
class NormalRebuildResult:
    """Result returned by :func:`rebuild_vertex_normals`."""

    normals: Tuple[Vec3, ...]
    weighting: str
    degenerate_face_indices: Tuple[int, ...]
    isolated_vertices: Tuple[int, ...]
    fallback_vertices: Tuple[int, ...]

    @property
    def degenerate_face_count(self) -> int:
        return len(self.degenerate_face_indices)

    @property
    def isolated_vertex_count(self) -> int:
        return len(self.isolated_vertices)

    @property
    def max_normal_length_error(self) -> float:
        return max_unit_length_error(self.normals)


@dataclass(frozen=True)
class WeightingComparison:
    """Numerical comparison between area and angle weighting."""

    area: NormalRebuildResult
    angle: NormalRebuildResult
    per_vertex_l2_difference: Tuple[float, ...]
    per_vertex_angle_difference_degrees: Tuple[float, ...]
    max_l2_difference: float
    mean_l2_difference: float
    max_angle_difference_degrees: float
    mean_angle_difference_degrees: float


def rebuild_vertex_normals(
    vertices: Sequence[Sequence[float]],
    faces: Sequence[Sequence[int]],
    weighting: str = "area",
    *,
    eps: float = _DEFAULT_EPS,
    fallback_normal: Sequence[float] = _DEFAULT_FALLBACK,
) -> NormalRebuildResult:
    """Rebuild unit vertex normals from triangle faces.

    Args:
        vertices: Sequence of 3D vertex coordinates.
        faces: Sequence of triangular faces, each containing three vertex
            indices. Face winding determines normal orientation.
        weighting: ``"area"`` or ``"angle"``.
        eps: Relative degeneracy tolerance. A face is rejected when
            ``||cross(e1, e2)|| <= eps * max_edge_length_squared``.
        fallback_normal: Unit normal assigned to vertices with no usable
            accumulated normal. Non-unit input is normalized.

    Returns:
        A :class:`NormalRebuildResult`. Degenerate faces are reported by
        index and do not contribute to any vertex normal.

    Raises:
        ValueError: If the mesh topology, weighting name, tolerance, or
            fallback normal is invalid.
    """

    if weighting not in {"area", "angle"}:
        raise ValueError("weighting must be 'area' or 'angle'")
    if not math.isfinite(eps) or eps < 0.0:
        raise ValueError("eps must be a finite non-negative number")

    parsed_vertices = tuple(_as_vec3(vertex, f"vertices[{index}]") for index, vertex in enumerate(vertices))
    parsed_faces = tuple(_as_face(face, f"faces[{index}]") for index, face in enumerate(faces))
    fallback = _normalize(_as_vec3(fallback_normal, "fallback_normal"))
    if fallback is None:
        raise ValueError("fallback_normal must be non-zero")

    accumulators = [[0.0, 0.0, 0.0] for _ in parsed_vertices]
    valid_incident_counts = [0] * len(parsed_vertices)
    degenerate_face_indices = []

    for face_index, face in enumerate(parsed_faces):
        _validate_face_indices(face, len(parsed_vertices), face_index)

        if len(set(face)) != 3:
            degenerate_face_indices.append(face_index)
            continue

        p0, p1, p2 = (parsed_vertices[face[0]], parsed_vertices[face[1]], parsed_vertices[face[2]])
        e1 = _sub(p1, p0)
        e2 = _sub(p2, p0)
        e3 = _sub(p2, p1)
        cross = _cross(e1, e2)
        cross_norm = _length(cross)
        edge_scale = max(_length_squared(e1), _length_squared(e2), _length_squared(e3))

        if cross_norm <= eps * edge_scale:
            degenerate_face_indices.append(face_index)
            continue

        for vertex_index in face:
            valid_incident_counts[vertex_index] += 1

        if weighting == "area":
            # The cross product magnitude is twice the triangle area, which is
            # equivalent to area weighting up to a global factor of two.
            for vertex_index in face:
                _add_in_place(accumulators[vertex_index], cross)
        else:
            unit_face_normal = _scale(cross, 1.0 / cross_norm)
            corner_vectors = (
                (_sub(p1, p0), _sub(p2, p0)),
                (_sub(p2, p1), _sub(p0, p1)),
                (_sub(p0, p2), _sub(p1, p2)),
            )
            for vertex_index, (u, v) in zip(face, corner_vectors):
                corner_angle = _angle_between(u, v)
                _add_in_place(accumulators[vertex_index], _scale(unit_face_normal, corner_angle))

    normals = []
    fallback_vertices = []
    for vertex_index, accumulated in enumerate(accumulators):
        normalized = _normalize(tuple(accumulated))
        if normalized is None:
            normals.append(fallback)
            fallback_vertices.append(vertex_index)
        else:
            normals.append(normalized)

    isolated_vertices = tuple(index for index, count in enumerate(valid_incident_counts) if count == 0)
    return NormalRebuildResult(
        normals=tuple(normals),
        weighting=weighting,
        degenerate_face_indices=tuple(degenerate_face_indices),
        isolated_vertices=isolated_vertices,
        fallback_vertices=tuple(fallback_vertices),
    )


def compare_weightings(
    vertices: Sequence[Sequence[float]],
    faces: Sequence[Sequence[int]],
    *,
    eps: float = _DEFAULT_EPS,
    fallback_normal: Sequence[float] = _DEFAULT_FALLBACK,
) -> WeightingComparison:
    """Rebuild normals with both weightings and return difference metrics."""

    area_result = rebuild_vertex_normals(
        vertices, faces, "area", eps=eps, fallback_normal=fallback_normal
    )
    angle_result = rebuild_vertex_normals(
        vertices, faces, "angle", eps=eps, fallback_normal=fallback_normal
    )

    l2_differences = tuple(
        _length(_sub(area_normal, angle_normal))
        for area_normal, angle_normal in zip(area_result.normals, angle_result.normals)
    )
    angle_differences = tuple(
        math.degrees(_angle_between(area_normal, angle_normal))
        for area_normal, angle_normal in zip(area_result.normals, angle_result.normals)
    )
    vertex_count = len(l2_differences)

    return WeightingComparison(
        area=area_result,
        angle=angle_result,
        per_vertex_l2_difference=l2_differences,
        per_vertex_angle_difference_degrees=angle_differences,
        max_l2_difference=max(l2_differences, default=0.0),
        mean_l2_difference=sum(l2_differences) / vertex_count if vertex_count else 0.0,
        max_angle_difference_degrees=max(angle_differences, default=0.0),
        mean_angle_difference_degrees=sum(angle_differences) / vertex_count if vertex_count else 0.0,
    )


def max_unit_length_error(normals: Sequence[Sequence[float]]) -> float:
    """Return ``max(abs(length(normal) - 1))`` for a normal sequence."""

    return max((abs(_length(_as_vec3(normal, "normal")) - 1.0) for normal in normals), default=0.0)


def _as_vec3(value: Sequence[float], name: str) -> Vec3:
    try:
        x, y, z = value
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain exactly three coordinates") from exc
    result = (float(x), float(y), float(z))
    if not all(math.isfinite(component) for component in result):
        raise ValueError(f"{name} must contain finite coordinates")
    return result


def _as_face(value: Sequence[int], name: str) -> Face:
    try:
        i, j, k = value
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain exactly three vertex indices") from exc
    face = (i, j, k)
    if any(not isinstance(index, int) or isinstance(index, bool) for index in face):
        raise ValueError(f"{name} must contain integer vertex indices")
    return face


def _validate_face_indices(face: Face, vertex_count: int, face_index: int) -> None:
    for vertex_index in face:
        if vertex_index < 0 or vertex_index >= vertex_count:
            raise ValueError(
                f"faces[{face_index}] references vertex {vertex_index}, "
                f"but the mesh has {vertex_count} vertices"
            )


def _add_in_place(target: List[float], value: Vec3) -> None:
    target[0] += value[0]
    target[1] += value[1]
    target[2] += value[2]


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale(a: Vec3, factor: float) -> Vec3:
    return (a[0] * factor, a[1] * factor, a[2] * factor)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _length_squared(a: Vec3) -> float:
    return _dot(a, a)


def _length(a: Vec3) -> float:
    return math.sqrt(_length_squared(a))


def _normalize(a: Vec3) -> Optional[Vec3]:
    length = _length(a)
    if length == 0.0:
        return None
    return _scale(a, 1.0 / length)


def _angle_between(a: Vec3, b: Vec3) -> float:
    return math.atan2(_length(_cross(a, b)), _dot(a, b))


def _demo_mesh() -> Tuple[Tuple[Vec3, ...], Tuple[Face, ...]]:
    vertices = (
        (0.0, 0.0, 0.15),
        (1.0, 0.0, 0.0),
        (0.8, 0.6, 0.35),
        (0.0, 1.0, -0.10),
        (-0.7, 0.7, 0.20),
        (-1.0, 0.0, -0.05),
        (-0.6, -0.8, 0.25),
        (0.0, -1.0, -0.15),
        (0.9, -0.4, 0.10),
        (0.0, 0.0, 2.0),  # isolated vertex
    )
    faces = (
        (0, 1, 2),
        (0, 2, 3),
        (0, 3, 4),
        (0, 4, 5),
        (0, 5, 6),
        (0, 6, 7),
        (0, 7, 8),
        (0, 8, 1),
        (0, 1, 1),  # repeated index, degenerate
        (0, 2, 2),  # repeated index, degenerate
    )
    return vertices, faces


def _format_float_list(values: Sequence[float]) -> str:
    return "[" + ", ".join(f"{value:.6f}" for value in values) + "]"


def main() -> None:
    vertices, faces = _demo_mesh()
    comparison = compare_weightings(vertices, faces)

    print("Mesh normal reconstruction demo")
    print("================================")
    print(f"vertices: {len(vertices)}")
    print(f"faces: {len(faces)}")
    print(f"degenerate faces: {comparison.area.degenerate_face_count} "
          f"at indices {list(comparison.area.degenerate_face_indices)}")
    print(f"isolated vertices: {list(comparison.area.isolated_vertices)}")
    print(f"max |length(normal)-1| (area): {comparison.area.max_normal_length_error:.3e}")
    print(f"max |length(normal)-1| (angle): {comparison.angle.max_normal_length_error:.3e}")
    print("area vs angle:")
    print(f"  max angular difference: {comparison.max_angle_difference_degrees:.6f} deg")
    print(f"  mean angular difference: {comparison.mean_angle_difference_degrees:.6f} deg")
    print(f"  max Euclidean difference: {comparison.max_l2_difference:.6f}")
    print(f"  mean Euclidean difference: {comparison.mean_l2_difference:.6f}")
    print("  per-vertex angular difference (deg): "
          f"{_format_float_list(comparison.per_vertex_angle_difference_degrees)}")


if __name__ == "__main__":
    main()
