"""Unit tests for mesh normal reconstruction (stdlib unittest only).

Run:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import meshes  # noqa: E402
from mesh_normals import (  # noqa: E402
    ANGLE,
    AREA,
    angular_difference,
    compare_weightings,
    rebuild_vertex_normals,
)

WEIGHTINGS = (AREA, ANGLE)


class TestPlaneGrid(unittest.TestCase):
    def setUp(self):
        self.verts, self.faces = meshes.plane_grid(8)

    def test_all_normals_point_up(self):
        for w in WEIGHTINGS:
            with self.subTest(weighting=w):
                r = rebuild_vertex_normals(self.verts, self.faces, w)
                for n in r.normals:
                    self.assertLess(
                        angular_difference(n, (0.0, 0.0, 1.0)), 1e-9)

    def test_no_degenerate_no_isolated(self):
        for w in WEIGHTINGS:
            with self.subTest(weighting=w):
                r = rebuild_vertex_normals(self.verts, self.faces, w)
                self.assertEqual(r.degenerate_faces, 0)
                self.assertEqual(r.isolated_vertices, ())
                self.assertEqual(r.valid_faces, len(self.faces))

    def test_unit_normals(self):
        for w in WEIGHTINGS:
            with self.subTest(weighting=w):
                r = rebuild_vertex_normals(self.verts, self.faces, w)
                r.assert_unit_normals(eps=1e-12)


class TestSphere(unittest.TestCase):
    def setUp(self):
        self.verts, self.faces = meshes.icosphere(2)

    def test_normals_are_radial(self):
        # On a unit sphere the exact vertex normal is the position itself.
        for w in WEIGHTINGS:
            with self.subTest(weighting=w):
                r = rebuild_vertex_normals(self.verts, self.faces, w)
                worst = max(
                    angular_difference(n, v)
                    for n, v in zip(r.normals, self.verts)
                )
                self.assertLess(worst, 5.0)  # degrees

    def test_angle_weighting_beats_area_on_uv_sphere(self):
        verts, faces = meshes.uv_sphere(24, 12)
        ra = rebuild_vertex_normals(verts, faces, AREA)
        rg = rebuild_vertex_normals(verts, faces, ANGLE)
        err_a = max(angular_difference(n, v)
                    for n, v in zip(ra.normals, verts))
        err_g = max(angular_difference(n, v)
                    for n, v in zip(rg.normals, verts))
        # Angle weighting is at least as accurate radially.
        self.assertLessEqual(err_g, err_a + 1e-9)

    def test_unit_normals(self):
        for w in WEIGHTINGS:
            with self.subTest(weighting=w):
                rebuild_vertex_normals(
                    self.verts, self.faces, w).assert_unit_normals()


class TestDegenerateFaces(unittest.TestCase):
    def setUp(self):
        (self.verts, self.faces,
         self.expected_degen, self.isolated) = (
            meshes.mesh_with_degenerate_and_isolated())

    def test_cull_count(self):
        for w in WEIGHTINGS:
            with self.subTest(weighting=w):
                r = rebuild_vertex_normals(self.verts, self.faces, w)
                self.assertEqual(r.degenerate_faces, self.expected_degen)
                self.assertEqual(
                    r.valid_faces, len(self.faces) - self.expected_degen)

    def test_degenerate_faces_do_not_pollute(self):
        # Surviving vertices of the flat grid must still point exactly +z.
        for w in WEIGHTINGS:
            with self.subTest(weighting=w):
                r = rebuild_vertex_normals(self.verts, self.faces, w)
                for idx, n in enumerate(r.normals):
                    if idx in r.isolated_vertices:
                        continue
                    self.assertLess(
                        angular_difference(n, (0.0, 0.0, 1.0)), 1e-9)

    def test_repeated_index_culled(self):
        verts = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
        faces = [(0, 1, 2), (0, 0, 1), (1, 2, 2)]
        r = rebuild_vertex_normals(verts, faces, AREA)
        self.assertEqual(r.degenerate_faces, 2)
        self.assertEqual(r.valid_faces, 1)

    def test_collinear_points_culled(self):
        verts = [(0, 0, 0), (1, 0, 0), (2, 0, 0), (0, 0, 1)]
        faces = [(0, 1, 2), (0, 1, 3)]
        r = rebuild_vertex_normals(verts, faces, ANGLE)
        self.assertEqual(r.degenerate_faces, 1)
        self.assertEqual(r.valid_faces, 1)

    def test_duplicate_coordinates_culled(self):
        verts = [(0, 0, 0), (1, 0, 0), (0, 0, 0), (0, 1, 0)]
        faces = [(0, 1, 2), (0, 1, 3)]  # first face: verts[0]==verts[2]
        r = rebuild_vertex_normals(verts, faces, AREA)
        self.assertEqual(r.degenerate_faces, 1)


class TestIsolatedVertex(unittest.TestCase):
    def test_isolated_vertex_gets_fallback(self):
        (verts, faces, _, isolated) = (
            meshes.mesh_with_degenerate_and_isolated())
        for w in WEIGHTINGS:
            with self.subTest(weighting=w):
                r = rebuild_vertex_normals(verts, faces, w)
                self.assertEqual(r.isolated_vertices, tuple(isolated))
                for idx in isolated:
                    self.assertEqual(r.normals[idx], (0.0, 0.0, 1.0))
                r.assert_unit_normals()

    def test_custom_fallback_is_normalized(self):
        verts = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (5, 5, 5)]
        faces = [(0, 1, 2)]
        r = rebuild_vertex_normals(
            verts, faces, AREA, isolated_normal=(0, 0, 7))
        self.assertEqual(r.isolated_vertices, (3,))
        self.assertAlmostEqual(r.normals[3][2], 1.0)
        r.assert_unit_normals()

    def test_zero_fallback_rejected(self):
        verts = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
        with self.assertRaises(ValueError):
            rebuild_vertex_normals(
                verts, [(0, 1, 2)], AREA, isolated_normal=(0, 0, 0))


class TestWeightingDifference(unittest.TestCase):
    def test_flat_mesh_schemes_agree(self):
        verts, faces = meshes.plane_grid(6)
        cmp_ = compare_weightings(verts, faces)
        self.assertLess(cmp_["max_angle_deg"], 1e-9)

    def test_tent_mesh_schemes_disagree(self):
        # Alternating ring radii 1/8: area weighting is dominated by the
        # huge outer triangles, angle weighting is not -> measurable gap.
        verts, faces = meshes.tent_mesh()
        cmp_ = compare_weightings(verts, faces)
        self.assertGreater(cmp_["max_angle_deg"], 1.0)

    def test_compare_reports_cull_counts(self):
        verts, faces, expected_degen, _ = (
            meshes.mesh_with_degenerate_and_isolated())
        cmp_ = compare_weightings(verts, faces)
        self.assertEqual(cmp_["degenerate_faces"], expected_degen)
        self.assertEqual(len(cmp_["isolated_vertices"]), 2)


class TestValidation(unittest.TestCase):
    def test_unknown_weighting(self):
        with self.assertRaises(ValueError):
            rebuild_vertex_normals([], [], "bogus")

    def test_non_triangle_face_rejected(self):
        verts = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)]
        with self.assertRaises(ValueError):
            rebuild_vertex_normals(verts, [(0, 1, 2, 3)], AREA)

    def test_out_of_range_index_rejected(self):
        verts = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
        with self.assertRaises(ValueError):
            rebuild_vertex_normals(verts, [(0, 1, 7)], AREA)

    def test_empty_mesh(self):
        r = rebuild_vertex_normals([], [], AREA)
        self.assertEqual(r.normals, [])
        self.assertEqual(r.degenerate_faces, 0)


if __name__ == "__main__":
    unittest.main()
