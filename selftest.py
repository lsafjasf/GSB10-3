"""Self-tests and measurements for imrotate.py.

Run:  python3 selftest.py            (writes artifacts into ./out)

Covers 0 deg, 90 deg, arbitrary angles, and out-of-canvas sampling;
reports interpolation difference metrics, inverse-mapping error data,
and crop-vs-expand boundary comparisons.  Exits non-zero on failure.
"""

import math
import os
import random
import sys

from imrotate import (
    PPMImage,
    forward_corner_bounds,
    map_backward,
    map_forward,
    rotate,
    rotated_size,
    sample_bilinear,
    sample_nearest,
    write_ppm,
)

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
BG = (0, 0, 0)


# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------


def channel_diffs(img_a, img_b):
    if (img_a.width, img_a.height) != (img_b.width, img_b.height):
        raise ValueError("image sizes differ")
    diffs = []
    for pa, pb in zip(img_a.pixels, img_b.pixels):
        for ch in range(3):
            diffs.append(abs(pa[ch] - pb[ch]))
    return diffs


def diff_metrics(img_a, img_b):
    diffs = channel_diffs(img_a, img_b)
    n = len(diffs)
    mae = sum(diffs) / n
    mse = sum(d * d for d in diffs) / n
    return {"mae": mae, "mse": mse, "rmse": math.sqrt(mse), "max": max(diffs)}


def fmt_metrics(metrics):
    return ("MAE=%.4f  MSE=%.4f  RMSE=%.4f  max=%d"
            % (metrics["mae"], metrics["mse"], metrics["rmse"],
               metrics["max"]))


def count_background(image, background=BG):
    return sum(1 for p in image.pixels if tuple(p) == tuple(background))


def count_exterior(image, width, height, theta, fit):
    """Destination pixels whose inverse-mapped source point is outside."""
    count = 0
    for y in range(image.height):
        for x in range(image.width):
            sx, sy = map_backward(x, y, width, height, theta, fit)
            if not (-0.5 <= sx <= width - 0.5 and -0.5 <= sy <= height - 0.5):
                count += 1
    return count


# ---------------------------------------------------------------------------
# Synthetic test image
# ---------------------------------------------------------------------------


def make_test_image(width=48, height=48):
    """Checkerboard + gradients + orientation markers (hard on NN)."""
    img = PPMImage(width, height)
    for y in range(height):
        for x in range(width):
            tile = ((x // 8) + (y // 8)) % 2
            base = 235 if tile else 45
            r = (base + x * 3) % 256
            g = (base + y * 3) % 256
            b = (base + (x + y) * 2) % 256
            img.set(x, y, (r, g, b))
    # bright square top-left: orientation marker
    for y in range(2, 8):
        for x in range(2, 8):
            img.set(x, y, (255, 255, 255))
    # red disc off-center: second orientation marker
    for y in range(height):
        for x in range(width):
            if (x - 34) ** 2 + (y - 12) ** 2 <= 16:
                img.set(x, y, (220, 30, 30))
    return img


def rotate90_cw_reference(image):
    """Independent exact 90 deg clockwise rotation (index arithmetic)."""
    w, h = image.width, image.height
    out = PPMImage(h, w)
    for y in range(h):
        for x in range(w):
            out.set(x, y, image.get(y, h - 1 - x))
    return out


# ---------------------------------------------------------------------------
# Test sections
# ---------------------------------------------------------------------------


def section(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def test_degenerate_angles(img):
    section("1. 0 deg / 90 deg exactness")
    ok = True

    for interp in ("nearest", "bilinear"):
        out = rotate(img, 0.0, interpolation=interp, fit="expand")
        exact = out.pixels == img.pixels
        print("0 deg  %-8s expand: size=%dx%d exact=%s"
              % (interp, out.width, out.height, exact))
        ok &= exact and (out.width, out.height) == (img.width, img.height)

    ref = rotate90_cw_reference(img)
    out90 = rotate(img, math.pi / 2, interpolation="nearest", fit="expand")
    exact90 = out90.pixels == ref.pixels
    print("90 deg nearest  expand: size=%dx%d exact vs index-reference=%s"
          % (out90.width, out90.height, exact90))
    ok &= exact90 and (out90.width, out90.height) == (img.height, img.width)

    out90b = rotate(img, math.pi / 2, interpolation="bilinear", fit="expand")
    m = diff_metrics(out90b, ref)
    print("90 deg bilinear expand: %s (exact=%s)"
          % (fmt_metrics(m), m["max"] == 0))
    ok &= m["max"] == 0

    out90c = rotate(img, math.pi / 2, interpolation="nearest", fit="crop")
    m = diff_metrics(out90c, ref)
    print("90 deg nearest  crop  : size=%dx%d exact vs reference=%s"
          % (out90c.width, out90c.height, m["max"] == 0))
    ok &= m["max"] == 0

    write_ppm(os.path.join(OUT_DIR, "rot90_nearest_expand.ppm"), out90)
    write_ppm(os.path.join(OUT_DIR, "rot0_bilinear_expand.ppm"),
              rotate(img, 0.0, interpolation="bilinear"))
    return ok


def test_interpolation_difference(img):
    section("2. Nearest vs bilinear interpolation difference")
    rows = []
    for deg in (15, 30, 45):
        theta = math.radians(deg)
        nn = rotate(img, theta, interpolation="nearest", fit="expand")
        bl = rotate(img, theta, interpolation="bilinear", fit="expand")
        m = diff_metrics(nn, bl)
        rows.append((deg, nn, bl, m))
        print("%2d deg expand: %s" % (deg, fmt_metrics(m)))
        write_ppm(os.path.join(OUT_DIR, "rot%02d_nearest_expand.ppm" % deg), nn)
        write_ppm(os.path.join(OUT_DIR, "rot%02d_bilinear_expand.ppm" % deg), bl)
    worst = max(r[3]["mse"] for r in rows)
    print("-> nearest and bilinear differ as expected (max MSE=%.2f)" % worst)
    return worst > 0.0


def test_inverse_mapping(img):
    section("3. Inverse mapping error data")
    ok = True
    rng = random.Random(20261004)

    # (a) algebraic: forward/backward must be exact inverses (float only)
    print("(a) algebraic map round-trip, 200 random points per case")
    for deg, fit in ((0, "expand"), (30, "expand"), (90, "expand"),
                     (137.5, "expand"), (45, "crop")):
        theta = math.radians(deg)
        out_w, out_h = (rotated_size(img.width, img.height, theta)
                        if fit == "expand" else (img.width, img.height))
        max_err = 0.0
        for _ in range(200):
            x = rng.uniform(0, out_w)
            y = rng.uniform(0, out_h)
            sx, sy = map_backward(x, y, img.width, img.height, theta, fit)
            bx, by = map_forward(sx, sy, img.width, img.height, theta, fit)
            max_err = max(max_err, abs(bx - x), abs(by - y))
        print("  %6.1f deg %-6s: max |forward(backward(p)) - p| = %.3e"
              % (deg, fit, max_err))
        ok &= max_err < 1e-9

    # (b) canvas: forward-mapped source corners vs computed canvas bounds
    print("(b) canvas corner residual (expand fit)")
    for deg in (0, 30, 90, 137.5):
        theta = math.radians(deg)
        min_x, min_y, max_x, max_y = forward_corner_bounds(
            img.width, img.height, theta)
        out_w, out_h = rotated_size(img.width, img.height, theta)
        residual = max(abs(min_x + 0.5), abs(min_y + 0.5),
                       abs(max_x - (out_w - 0.5)),
                       abs(max_y - (out_h - 0.5)))
        print("  %6.1f deg: canvas=%dx%d corner residual=%.4f px"
              % (deg, out_w, out_h, residual))
        ok &= residual <= 0.5 + 1e-9

    # (c) image round-trip rotate(+t) then rotate(-t), center-cropped
    print("(c) image round-trip rotate(+t)->rotate(-t) vs original")
    for deg, interp in ((30, "nearest"), (30, "bilinear"), (45, "bilinear")):
        theta = math.radians(deg)
        fwd = rotate(img, theta, interpolation=interp, fit="expand")
        back_full = rotate(fwd, -theta, interpolation=interp, fit="crop")
        off_x = (back_full.width - img.width) // 2
        off_y = (back_full.height - img.height) // 2
        back = PPMImage(img.width, img.height, [
            back_full.get(x + off_x, y + off_y)
            for y in range(img.height) for x in range(img.width)])
        m = diff_metrics(back, img)
        print("  %2d deg %-8s round-trip: %s" % (deg, interp, fmt_metrics(m)))
        write_ppm(os.path.join(
            OUT_DIR, "roundtrip_%02d_%s.ppm" % (deg, interp)), back)
    return ok


def test_boundary(img):
    section("4. Boundary strategies: crop vs expand, constant vs clamp")
    ok = True
    deg = 30
    theta = math.radians(deg)

    exp_nn = rotate(img, theta, interpolation="nearest", fit="expand")
    crp_nn = rotate(img, theta, interpolation="nearest", fit="crop")
    exp_bl = rotate(img, theta, interpolation="bilinear", fit="expand")
    crp_bl = rotate(img, theta, interpolation="bilinear", fit="crop")

    # (a) expand canvas contains the whole rotated image; crop discards it
    ext_exp = count_exterior(exp_nn, img.width, img.height, theta, "expand")
    ext_crp = count_exterior(crp_nn, img.width, img.height, theta, "crop")
    bg_exp = count_background(exp_nn)
    bg_crp = count_background(crp_nn)
    print("(a) %d deg nearest: expand %dx%d exterior=%d bg=%d | "
          "crop %dx%d exterior=%d bg=%d"
          % (deg, exp_nn.width, exp_nn.height, ext_exp, bg_exp,
             crp_nn.width, crp_nn.height, ext_crp, bg_crp))
    ok &= bg_exp == ext_exp > 0  # every exterior pixel got background
    ok &= bg_crp == ext_crp > 0

    # (b) crop == center crop of expand (same center, even dimensions)
    off = (exp_nn.width - crp_nn.width) // 2
    same = all(
        crp_nn.get(x, y) == exp_nn.get(x + off, y + off)
        for y in range(crp_nn.height) for x in range(crp_nn.width))
    print("(b) crop equals center-crop of expand (offset=%d): %s" % (off, same))
    ok &= same

    # (c) edge mode: constant vs clamp at the border
    clamp_bl = rotate(img, theta, interpolation="bilinear", fit="expand",
                      edge="clamp")
    m = diff_metrics(exp_bl, clamp_bl)
    print("(c) bilinear expand edge=constant vs edge=clamp: %s"
          % fmt_metrics(m))
    ok &= m["max"] > 0
    write_ppm(os.path.join(OUT_DIR, "rot30_bilinear_expand_clamp.ppm"), clamp_bl)

    write_ppm(os.path.join(OUT_DIR, "rot30_nearest_crop.ppm"), crp_nn)
    write_ppm(os.path.join(OUT_DIR, "rot30_bilinear_crop.ppm"), crp_bl)
    return ok


def test_out_of_canvas():
    section("5. Out-of-canvas sampling")
    ok = True
    img = PPMImage(2, 2, [(10, 0, 0), (20, 0, 0), (30, 0, 0), (40, 0, 0)])

    nn_in = sample_nearest(img, 0.0, 0.0)
    nn_round = sample_nearest(img, 0.6, 0.6)
    nn_out = sample_nearest(img, -3.2, 9.9, background=(7, 7, 7))
    nn_clamp = sample_nearest(img, -3.2, 9.9, edge="clamp")
    print("nearest  in-bounds=%s round=%s constant-oob=%s clamp-oob=%s"
          % (nn_in, nn_round, nn_out, nn_clamp))
    ok &= nn_in == (10, 0, 0) and nn_round == (40, 0, 0)
    ok &= nn_out == (7, 7, 7)
    ok &= nn_clamp == (30, 0, 0)  # clamps to bottom-left pixel

    bl_edge = sample_bilinear(img, -0.5, 0.0, background=(0, 0, 0))
    bl_clamp = sample_bilinear(img, -0.5, 0.0, edge="clamp")
    bl_far = sample_bilinear(img, 100.0, 100.0, background=(5, 5, 5))
    print("bilinear edge constant=%s clamp=%s far-oob=%s"
          % (bl_edge, bl_clamp, bl_far))
    ok &= bl_edge == (5, 0, 0)        # half weight on (10,0,0), half bg
    ok &= bl_clamp == (10, 0, 0)      # both taps clamp to the corner
    ok &= bl_far == (5, 5, 5)         # fully outside -> pure background

    # rotating a tiny image 45 deg: corners of the expanded canvas are
    # outside the source -> constant background fill
    rot = rotate(img, math.pi / 4, interpolation="nearest", fit="expand")
    print("2x2 rotated 45 deg expand: size=%dx%d bg-pixels=%d/%d"
          % (rot.width, rot.height, count_background(rot, (0, 0, 0)),
             rot.width * rot.height))
    ok &= (rot.width, rot.height) == (3, 3)
    ok &= count_background(rot, (0, 0, 0)) > 0
    return ok


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    img = make_test_image()
    write_ppm(os.path.join(OUT_DIR, "input.ppm"), img)

    results = [
        test_degenerate_angles(img),
        test_interpolation_difference(img),
        test_inverse_mapping(img),
        test_boundary(img),
        test_out_of_canvas(),
    ]
    section("Summary")
    names = ["0/90 deg exactness", "interpolation difference",
             "inverse mapping", "boundary strategies", "out-of-canvas"]
    for name, passed in zip(names, results):
        print("%-28s %s" % (name, "PASS" if passed else "FAIL"))
    if all(results):
        print("ALL TESTS PASSED")
        return 0
    print("SOME TESTS FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
