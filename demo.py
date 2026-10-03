#!/usr/bin/env python3
"""Self-running demo: weighting comparison, cull counts, norm errors.

Usage:  python3 demo.py
Only the Python standard library is required.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import meshes  # noqa: E402
from mesh_normals import (  # noqa: E402
    ANGLE,
    AREA,
    angular_difference,
    compare_weightings,
    rebuild_vertex_normals,
)


def section(title: str) -> None:
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def report_cmp(name, verts, faces):
    cmp_ = compare_weightings(verts, faces)
    ra, rg = cmp_["area_result"], cmp_["angle_result"]
    print(f"\n[{name}]  vertices={len(verts)}  faces={ra.total_faces}")
    print(f"  degenerate culled : {cmp_['degenerate_faces']}")
    print(f"  valid faces       : {cmp_['valid_faces']}")
    print(f"  isolated vertices : {len(cmp_['isolated_vertices'])}"
          f"  {cmp_['isolated_vertices'][:8]}")
    print(f"  area  weighting max ||n||-1 : {ra.max_norm_error():.3e}")
    print(f"  angle weighting max ||n||-1 : {rg.max_norm_error():.3e}")
    print("  area vs angle normal difference:")
    print(f"    max  angle (all)    : {cmp_['max_angle_deg']:.4f} deg")
    print(f"    mean angle (all)    : {cmp_['mean_angle_deg']:.4f} deg")
    print(f"    max  angle (active) : {cmp_['max_angle_active_deg']:.4f} deg")
    print(f"    mean angle (active) : {cmp_['mean_angle_active_deg']:.4f} deg")
    print(f"    vertices >1 deg     : {cmp_['vertices_over_1deg']}")
    print(f"    vertices >5 deg     : {cmp_['vertices_over_5deg']}")
    return cmp_


def main() -> int:
    section("1. weighting comparison: area vs angle")
    cmp_plane = report_cmp("regular plane grid", *meshes.plane_grid(8))
    cmp_ico = report_cmp("icosphere subdiv=2", *meshes.icosphere(2))
    cmp_uv = report_cmp("uv-sphere (pole slivers)", *meshes.uv_sphere())
    cmp_tent = report_cmp("tent mesh (1x vs 8x radii)",
                          *meshes.tent_mesh())

    print("\n  takeaway:")
    print("    - on regular/flat tessellation the two schemes agree "
          "(~0 deg);")
    print("    - on uneven triangles (uv-sphere poles, tent mesh) the apex")
    print("      normals differ, area weighting is pulled toward the big")
    print("      triangles and creates visible striping.")

    section("2. degenerate-triangle culling")
    verts, faces, expected_degen, isolated_idx = (
        meshes.mesh_with_degenerate_and_isolated())
    print(f"\ninput faces            : {len(faces)}")
    print(f"expected degenerate    : {expected_degen}")
    for w in (AREA, ANGLE):
        r = rebuild_vertex_normals(verts, faces, w)
        print(f"\nweighting={w}")
        print(f"  culled degenerate    : {r.degenerate_faces}")
        print(f"  kept valid faces     : {r.valid_faces}")
        print(f"  isolated vertices    : {list(r.isolated_vertices)}")
        print(f"  max ||n|| - 1        : {r.max_norm_error():.3e}")
        r.assert_unit_normals()
        print("  assert_unit_normals  : PASS")
        # Isolated vertices (unused, or used only by culled faces) get
        # the fallback normal +z.
        for idx in isolated_idx:
            assert r.normals[idx] == (0.0, 0.0, 1.0)
        # Every surviving vertex on the flat grid still points +z even
        # though the mesh was polluted with bad faces.
        isolated_set = set(isolated_idx)
        for idx in range(len(verts)):
            if idx in isolated_set:
                continue
            assert angular_difference(r.normals[idx], (0, 0, 1)) < 1e-9
        print("  all active normals == +z (pollution ignored): PASS")

    assert cmp_plane["degenerate_faces"] == 0
    assert cmp_ico["degenerate_faces"] == 0
    assert cmp_uv["degenerate_faces"] == 0
    assert cmp_tent["degenerate_faces"] == 0

    section("3. all checks passed")
    print(f"plane max discrepancy   : {cmp_plane['max_angle_deg']:.3e} deg")
    print(f"icosphere max discrepancy: {cmp_ico['max_angle_deg']:.4f} deg")
    print(f"uv-sphere max discrepancy: {cmp_uv['max_angle_deg']:.4f} deg")
    print(f"tent max discrepancy    : {cmp_tent['max_angle_deg']:.4f} deg "
          f"(at the apex)")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
