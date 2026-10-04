import math
import unittest

from mesh_normals import compare_weightings, rebuild_vertex_normals


def make_plane_grid(size=3):
    vertices = [(float(x), float(y), 0.0) for y in range(size) for x in range(size)]
    faces = []
    for y in range(size - 1):
        for x in range(size - 1):
            v00 = y * size + x
            v10 = v00 + 1
            v01 = v00 + size
            v11 = v01 + 1
            faces.append((v00, v10, v11))
            faces.append((v00, v11, v01))
    return vertices, faces


def make_uv_sphere(segments=16, rings=8):
    vertices = [(0.0, 0.0, 1.0)]
    for ring in range(1, rings):
        theta = math.pi * ring / rings
        sin_theta = math.sin(theta)
        z = math.cos(theta)
        for segment in range(segments):
            phi = 2.0 * math.pi * segment / segments
            vertices.append((sin_theta * math.cos(phi), sin_theta * math.sin(phi), z))
    vertices.append((0.0, 0.0, -1.0))
    bottom = len(vertices) - 1

    faces = []
    for segment in range(segments):
        current = 1 + segment
        following = 1 + (segment + 1) % segments
        faces.append((0, current, following))

    for ring in range(rings - 2):
        upper = 1 + ring * segments
        lower = upper + segments
        for segment in range(segments):
            following = (segment + 1) % segments
            a = upper + segment
            b = upper + following
            c = lower + segment
            d = lower + following
            faces.append((a, c, b))
            faces.append((b, c, d))

    last_ring = 1 + (rings - 2) * segments
    for segment in range(segments):
        current = last_ring + segment
        following = last_ring + (segment + 1) % segments
        faces.append((bottom, following, current))

    return vertices, faces


def make_irregular_fan():
    vertices = [
        (0.0, 0.0, 0.15),
        (1.0, 0.0, 0.0),
        (0.8, 0.6, 0.35),
        (0.0, 1.0, -0.10),
        (-0.7, 0.7, 0.20),
        (-1.0, 0.0, -0.05),
        (-0.6, -0.8, 0.25),
        (0.0, -1.0, -0.15),
        (0.9, -0.4, 0.10),
    ]
    faces = [
        (0, 1, 2),
        (0, 2, 3),
        (0, 3, 4),
        (0, 4, 5),
        (0, 5, 6),
        (0, 6, 7),
        (0, 7, 8),
        (0, 8, 1),
    ]
    return vertices, faces


class MeshNormalTests(unittest.TestCase):
    def assert_unit_normals(self, result):
        self.assertLessEqual(result.max_normal_length_error, 1.0e-12)

    def test_plane_grid_area_and_angle(self):
        vertices, faces = make_plane_grid(size=4)
        for weighting in ("area", "angle"):
            with self.subTest(weighting=weighting):
                result = rebuild_vertex_normals(vertices, faces, weighting)
                self.assertEqual(result.degenerate_face_count, 0)
                self.assertEqual(result.isolated_vertices, ())
                self.assert_unit_normals(result)
                for normal in result.normals:
                    self.assertAlmostEqual(normal[0], 0.0, delta=1.0e-12)
                    self.assertAlmostEqual(normal[1], 0.0, delta=1.0e-12)
                    self.assertAlmostEqual(normal[2], 1.0, delta=1.0e-12)

    def test_sphere_normals_follow_radial_direction(self):
        vertices, faces = make_uv_sphere(segments=32, rings=16)
        for weighting in ("area", "angle"):
            with self.subTest(weighting=weighting):
                result = rebuild_vertex_normals(vertices, faces, weighting)
                self.assertEqual(result.degenerate_face_count, 0)
                self.assertEqual(result.isolated_vertices, ())
                self.assert_unit_normals(result)
                dots = [
                    sum(normal[axis] * vertex[axis] for axis in range(3))
                    for normal, vertex in zip(result.normals, vertices)
                ]
                self.assertGreater(min(dots), 0.999)

    def test_degenerate_faces_are_excluded_and_counted(self):
        vertices = [
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (1.0, 1.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0),  # repeated coordinates at a distinct index
            (0.5, 0.0, 0.0),  # collinear with vertices 0 and 1
        ]
        valid_faces = [(0, 1, 2), (0, 2, 3)]
        degenerate_faces = [
            (0, 0, 1),  # repeated index
            (0, 1, 4),  # repeated coordinates
            (0, 1, 5),  # zero-area collinear triangle
        ]
        faces = valid_faces + degenerate_faces

        for weighting in ("area", "angle"):
            with self.subTest(weighting=weighting):
                result = rebuild_vertex_normals(vertices, faces, weighting)
                clean = rebuild_vertex_normals(vertices, valid_faces, weighting)
                self.assertEqual(result.degenerate_face_count, 3)
                self.assertEqual(result.degenerate_face_indices, (2, 3, 4))
                self.assertEqual(result.normals[:4], clean.normals[:4])
                self.assertEqual(result.isolated_vertices, (4, 5))
                self.assert_unit_normals(result)

    def test_isolated_vertex_gets_unit_fallback(self):
        vertices = [
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (2.0, 2.0, 0.0),
        ]
        result = rebuild_vertex_normals(vertices, [(0, 1, 2)], "angle")
        self.assertEqual(result.degenerate_face_count, 0)
        self.assertEqual(result.isolated_vertices, (3,))
        self.assertEqual(result.fallback_vertices, (3,))
        self.assertEqual(result.normals[3], (0.0, 0.0, 1.0))
        self.assert_unit_normals(result)

    def test_weighting_comparison_reports_real_difference(self):
        vertices, faces = make_irregular_fan()
        comparison = compare_weightings(vertices, faces)
        self.assertEqual(comparison.area.degenerate_face_count, 0)
        self.assertEqual(comparison.angle.degenerate_face_count, 0)
        self.assert_unit_normals(comparison.area)
        self.assert_unit_normals(comparison.angle)
        self.assertGreater(comparison.max_angle_difference_degrees, 0.1)
        self.assertGreater(comparison.max_l2_difference, 0.0)
        self.assertLessEqual(comparison.mean_angle_difference_degrees,
                             comparison.max_angle_difference_degrees)

    def test_empty_mesh_is_valid(self):
        for weighting in ("area", "angle"):
            with self.subTest(weighting=weighting):
                result = rebuild_vertex_normals([], [], weighting)
                self.assertEqual(result.normals, ())
                self.assertEqual(result.degenerate_face_count, 0)
                self.assertEqual(result.max_normal_length_error, 0.0)

    def test_invalid_input_raises_value_error(self):
        with self.assertRaises(ValueError):
            rebuild_vertex_normals([(0, 0, 0)], [], "unsupported")
        with self.assertRaises(ValueError):
            rebuild_vertex_normals([(0, 0, 0)], [(0, 1)], "area")
        with self.assertRaises(ValueError):
            rebuild_vertex_normals([(0, 0, 0)], [(0, 1, 2)], "area")
        with self.assertRaises(ValueError):
            rebuild_vertex_normals(
                [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
                [(0, 1, 2)],
                "area",
                fallback_normal=(0, 0, 0),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
