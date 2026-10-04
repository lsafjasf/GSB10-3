"""Image rotation — nearest-neighbor and bilinear interpolation.

Pure Python 3, standard library only.  Images are represented by
:class:`PPMImage` (flat RGB rows, 8-bit channels).  PPM (P6) is used on
disk so no third-party imaging library is needed.

Coordinate convention
---------------------
Pixel ``(x, y)`` sits at integer coordinate ``(x, y)``; the canvas spans
``[-0.5, W-0.5] x [-0.5, H-0.5]``.  Rotation is clockwise for positive
``theta`` (the y axis points downward on a raster image), performed
about the image center ``((W-1)/2, (H-1)/2)``.

Forward map (source center -> destination center)::

    dx = cdx + cos(t) * (sx - cx) - sin(t) * (sy - cy)
    dy = cdy + sin(t) * (sx - cx) + cos(t) * (sy - cy)

The rasterizer applies the *exact inverse* of this affine map at every
destination pixel center, so the coordinate mapping is mathematically
invertible; only floating-point round-off and interpolation resampling
add error.

Boundary handling
-----------------
``fit`` selects the canvas strategy:

* ``expand`` — grow the canvas to the rotated bounding box; samples
  landing outside the source get ``background`` (constant) or the
  clamped edge (``edge='clamp'``).
* ``crop``   — keep the original canvas size and rotate about the same
  center; content rotated outside the window is discarded.

``edge`` selects sample handling outside the source image:

* ``constant`` — out-of-range taps take ``background``.
* ``clamp``    — taps are clamped to the nearest edge pixel (replicate).
"""

import argparse
import math
import sys


# ---------------------------------------------------------------------------
# Image container and PPM (P6) IO
# ---------------------------------------------------------------------------


class PPMImage:
    """In-memory RGB image; ``pixels`` is a flat list of (r, g, b) tuples."""

    def __init__(self, width, height, pixels=None):
        self.width = width
        self.height = height
        if pixels is None:
            self.pixels = [(0, 0, 0)] * (width * height)
        else:
            if len(pixels) != width * height:
                raise ValueError("pixel count does not match dimensions")
            self.pixels = list(pixels)

    def get(self, x, y):
        return self.pixels[y * self.width + x]

    def set(self, x, y, value):
        self.pixels[y * self.width + x] = value


def read_ppm(path):
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:2] != b"P6":
        raise ValueError("%s is not a binary PPM (P6) file" % path)
    index = 2
    header = []
    while len(header) < 3:
        while index < len(data) and data[index : index + 1].isspace():
            index += 1
        if data[index : index + 1] == b"#":
            while index < len(data) and data[index] not in (10, 13):
                index += 1
            continue
        start = index
        while (
            index < len(data)
            and not data[index : index + 1].isspace()
            and data[index : index + 1] != b"#"
        ):
            index += 1
        header.append(int(data[start:index]))
    index += 1  # single whitespace separating header from raw pixels
    width, height, maxval = header
    if maxval != 255:
        raise ValueError("only maxval=255 PPM files are supported")
    raw = data[index : index + width * height * 3]
    if len(raw) != width * height * 3:
        raise ValueError("truncated PPM pixel data")
    triples = [
        (raw[i], raw[i + 1], raw[i + 2]) for i in range(0, len(raw), 3)
    ]
    return PPMImage(width, height, triples)


def write_ppm(path, image):
    header = "P6\n%d %d\n255\n" % (image.width, image.height)
    raw = bytearray(header.encode("ascii"))
    raw.extend(channel for pixel in image.pixels for channel in pixel)
    with open(path, "wb") as handle:
        handle.write(bytes(raw))


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def _round_half_up(value):
    """Round to nearest non-negative integer (ties upward)."""
    return int(math.floor(value + 0.5))


def rotated_size(width, height, theta):
    """Canvas size that contains the rotated image with no clipping."""
    cos_a = abs(math.cos(theta))
    sin_a = abs(math.sin(theta))
    out_w = width * cos_a + height * sin_a
    out_h = width * sin_a + height * cos_a
    return _round_half_up(out_w), _round_half_up(out_h)


def _geometry(width, height, theta, fit):
    """Return (cos, sin, src center, dst center, out size)."""
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    if fit == "expand":
        out_w, out_h = rotated_size(width, height, theta)
    elif fit == "crop":
        out_w, out_h = width, height
    else:
        raise ValueError("fit must be 'expand' or 'crop', got %r" % fit)
    src_cx = (width - 1) / 2.0
    src_cy = (height - 1) / 2.0
    dst_cx = (out_w - 1) / 2.0
    dst_cy = (out_h - 1) / 2.0
    return cos_t, sin_t, (src_cx, src_cy), (dst_cx, dst_cy), (out_w, out_h)


def map_backward(x, y, width, height, theta, fit):
    """Destination pixel-center -> continuous source coordinate.

    This is the exact inverse of :func:`map_forward`.
    """
    cos_t, sin_t, (cx, cy), (dx, dy), _ = _geometry(width, height, theta, fit)
    rx = x - dx
    ry = y - dy
    sx = cx + cos_t * rx + sin_t * ry
    sy = cy - sin_t * rx + cos_t * ry
    return sx, sy


def map_forward(x, y, width, height, theta, fit):
    """Continuous source coordinate -> destination coordinate."""
    cos_t, sin_t, (cx, cy), (dx, dy), _ = _geometry(width, height, theta, fit)
    rx = x - cx
    ry = y - cy
    ox = dx + cos_t * rx - sin_t * ry
    oy = dy + sin_t * rx + cos_t * ry
    return ox, oy


def forward_corner_bounds(width, height, theta):
    """Rotated bounding box of the four source-canvas outer corners."""
    corners = [(-0.5, -0.5), (width - 0.5, -0.5),
               (width - 0.5, height - 0.5), (-0.5, height - 0.5)]
    xs, ys = [], []
    for x, y in corners:
        ox, oy = map_forward(x, y, width, height, theta, "expand")
        xs.append(ox)
        ys.append(oy)
    return min(xs), min(ys), max(xs), max(ys)


# ---------------------------------------------------------------------------
# Sampling and rotation
# ---------------------------------------------------------------------------


def _clamp_index(index, size):
    if index < 0:
        return 0
    if index >= size:
        return size - 1
    return index


def sample_nearest(image, sx, sy, background=(0, 0, 0), edge="constant"):
    """Nearest-neighbor sample at a continuous source coordinate."""
    ix = int(math.floor(sx + 0.5))
    iy = int(math.floor(sy + 0.5))
    if 0 <= ix < image.width and 0 <= iy < image.height:
        return image.get(ix, iy)
    if edge == "clamp":
        ix = _clamp_index(ix, image.width)
        iy = _clamp_index(iy, image.height)
        return image.get(ix, iy)
    if edge == "constant":
        return tuple(background)
    raise ValueError("edge must be 'constant' or 'clamp', got %r" % edge)


def sample_bilinear(image, sx, sy, background=(0, 0, 0), edge="constant"):
    """Bilinear sample at a continuous source coordinate."""
    x0 = int(math.floor(sx))
    y0 = int(math.floor(sy))
    x1 = x0 + 1
    y1 = y0 + 1
    wx = sx - x0
    wy = sy - y0
    taps = (
        (x0, y0, (1.0 - wx) * (1.0 - wy)),
        (x1, y0, wx * (1.0 - wy)),
        (x0, y1, (1.0 - wx) * wy),
        (x1, y1, wx * wy),
    )
    acc = [0.0, 0.0, 0.0]
    weight_sum = 0.0
    for tx, ty, weight in taps:
        if 0 <= tx < image.width and 0 <= ty < image.height:
            value = image.get(tx, ty)
        elif edge == "clamp":
            tx = _clamp_index(tx, image.width)
            ty = _clamp_index(ty, image.height)
            value = image.get(tx, ty)
        elif edge == "constant":
            value = background
        else:
            raise ValueError("edge must be 'constant' or 'clamp'")
        for channel in range(3):
            acc[channel] += weight * value[channel]
        weight_sum += weight
    if edge == "constant" and weight_sum < 1.0:
        missing = 1.0 - weight_sum
        for channel in range(3):
            acc[channel] += missing * background[channel]
    return tuple(_round_half_up(acc[channel]) for channel in range(3))


def rotate(image, theta, interpolation="bilinear", fit="expand",
           background=(0, 0, 0), edge="constant"):
    """Rotate ``image`` clockwise by ``theta`` radians.

    interpolation: 'nearest' | 'bilinear'
    fit:           'expand'  | 'crop'
    edge:          'constant'| 'clamp'
    """
    if interpolation not in ("nearest", "bilinear"):
        raise ValueError("unsupported interpolation %r" % interpolation)
    sampler = (sample_nearest if interpolation == "nearest"
               else sample_bilinear)
    _, _, _, _, (out_w, out_h) = _geometry(
        image.width, image.height, theta, fit)
    out = PPMImage(out_w, out_h)
    for y in range(out_h):
        for x in range(out_w):
            sx, sy = map_backward(
                x, y, image.width, image.height, theta, fit)
            out.set(x, y, sampler(image, sx, sy, background, edge))
    return out


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _main(argv=None):
    parser = argparse.ArgumentParser(description="Rotate a PPM (P6) image.")
    parser.add_argument("input", help="input PPM (P6) file")
    parser.add_argument("output", help="output PPM (P6) file")
    parser.add_argument("--angle", type=float, required=True,
                        help="clockwise rotation angle in degrees")
    parser.add_argument("--interpolation", choices=("nearest", "bilinear"),
                        default="bilinear")
    parser.add_argument("--fit", choices=("expand", "crop"), default="expand")
    parser.add_argument("--edge", choices=("constant", "clamp"),
                        default="constant")
    parser.add_argument("--background", type=int, nargs=3, default=(0, 0, 0),
                        metavar=("R", "G", "B"))
    args = parser.parse_args(argv)
    image = read_ppm(args.input)
    result = rotate(
        image, math.radians(args.angle),
        interpolation=args.interpolation, fit=args.fit,
        background=tuple(args.background), edge=args.edge)
    write_ppm(args.output, result)
    print("%s -> %s (%dx%d)" % (
        args.input, args.output, result.width, result.height))


if __name__ == "__main__":
    sys.exit(_main())
