"""Image rotation with pure Python standard library only.

Features:
  - nearest-neighbor and bilinear interpolation
  - two canvas strategies: "crop" (keep size) and "expand" (grow to fit)
  - two out-of-bounds strategies: "constant" fill and "edge" replication
  - explicit forward / inverse coordinate maps about the canvas center

Images are single-channel grayscale, pixels stored as float rows, range 0..255.
"""

import math


class Image:
    def __init__(self, width, height, data=None):
        self.width = width
        self.height = height
        if data is None:
            self.data = [[0.0] * width for _ in range(height)]
        else:
            self.data = [list(row) for row in data]

    def copy(self):
        return Image(self.width, self.height, self.data)

    def get(self, x, y):
        return self.data[y][x]

    def set(self, x, y, value):
        self.data[y][x] = value


def canvas_size(width, height, angle_deg):
    """Smallest canvas (in pixel-center units) that holds a rotated image."""
    angle = math.radians(angle_deg)
    cos_a = abs(math.cos(angle))
    sin_a = abs(math.sin(angle))
    span_x = (width - 1) * cos_a + (height - 1) * sin_a
    span_y = (width - 1) * sin_a + (height - 1) * cos_a
    return int(math.ceil(span_x - 1e-9)) + 1, int(math.ceil(span_y - 1e-9)) + 1


def _centers(src_w, src_h, dst_w, dst_h):
    return ((src_w - 1) / 2.0, (src_h - 1) / 2.0,
            (dst_w - 1) / 2.0, (dst_h - 1) / 2.0)


def forward_map(x, y, src_w, src_h, dst_w, dst_h, angle_deg):
    """Source pixel center -> destination pixel center (rotation by +angle)."""
    angle = math.radians(angle_deg)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    cx, cy, Cx, Cy = _centers(src_w, src_h, dst_w, dst_h)
    dx = x - cx
    dy = y - cy
    return (Cx + dx * cos_a - dy * sin_a,
            Cy + dx * sin_a + dy * cos_a)


def inverse_map(x, y, src_w, src_h, dst_w, dst_h, angle_deg):
    """Destination pixel center -> source pixel center (rotation by -angle)."""
    angle = math.radians(angle_deg)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    cx, cy, Cx, Cy = _centers(src_w, src_h, dst_w, dst_h)
    dx = x - Cx
    dy = y - Cy
    return (cx + dx * cos_a + dy * sin_a,
            cy - dx * sin_a + dy * cos_a)


def _sample_nearest(image, sx, sy, border, fill):
    x = int(round(sx))
    y = int(round(sy))
    if x < 0 or x >= image.width or y < 0 or y >= image.height:
        if border == "edge":
            x = min(max(x, 0), image.width - 1)
            y = min(max(y, 0), image.height - 1)
        else:
            return fill
    return image.data[y][x]


def _sample_bilinear(image, sx, sy, border, fill):
    x0 = int(math.floor(sx))
    y0 = int(math.floor(sy))
    x1 = x0 + 1
    y1 = y0 + 1
    tx = sx - x0
    ty = sy - y0

    eps = 1e-9
    if (sx < -eps or sx > image.width - 1 + eps
            or sy < -eps or sy > image.height - 1 + eps):
        if border != "edge":
            return fill

    def clamped(px, py):
        px = min(max(px, 0), image.width - 1)
        py = min(max(py, 0), image.height - 1)
        return image.data[py][px]

    v00 = clamped(x0, y0)
    v10 = clamped(x1, y0)
    v01 = clamped(x0, y1)
    v11 = clamped(x1, y1)
    a = v00 * (1.0 - tx) + v10 * tx
    b = v01 * (1.0 - tx) + v11 * tx
    return a * (1.0 - ty) + b * ty


def rotate(image, angle_deg, interp="bilinear", mode="expand",
           border="constant", fill=0.0, out_size=None):
    """Rotate ``image`` counter-clockwise by ``angle_deg`` degrees.

    interp: "nearest" or "bilinear"
    mode:   "crop" (output keeps source size) or "expand" (grow to fit)
    border: "constant" (use fill) or "edge" (replicate nearest border pixel)
    out_size: optional explicit (width, height); overrides mode.
    """
    if interp == "nearest":
        sample = _sample_nearest
    elif interp == "bilinear":
        sample = _sample_bilinear
    else:
        raise ValueError("interp must be 'nearest' or 'bilinear'")

    if out_size is not None:
        dst_w, dst_h = out_size
    elif mode == "expand":
        dst_w, dst_h = canvas_size(image.width, image.height, angle_deg)
    elif mode == "crop":
        dst_w, dst_h = image.width, image.height
    else:
        raise ValueError("mode must be 'crop' or 'expand'")

    result = Image(dst_w, dst_h)
    for y in range(dst_h):
        row = result.data[y]
        for x in range(dst_w):
            sx, sy = inverse_map(x, y, image.width, image.height,
                                 dst_w, dst_h, angle_deg)
            row[x] = sample(image, sx, sy, border, fill)
    return result


def _read_tokens(text):
    tokens = []
    for raw in text.split():
        tokens.append(raw)
    return tokens


def read_pgm(path):
    """Read P2 (ASCII) or P5 (binary) PGM; returns Image with float pixels."""
    with open(path, "rb") as handle:
        raw = handle.read()

    header_parts = []
    index = 0

    def next_token():
        nonlocal index
        while index < len(raw) and raw[index] in b" \t\n\r":
            index += 1
        start = index
        while index < len(raw) and raw[index] not in b" \t\n\r":
            index += 1
        return raw[start:index]

    magic = next_token()
    if magic not in (b"P2", b"P5"):
        raise ValueError("only P2/P5 PGM is supported, got %r" % magic)
    width = int(next_token())
    height = int(next_token())
    maxval = int(next_token())
    index += 1

    image = Image(width, height)
    if magic == b"P5":
        if maxval > 255:
            raise ValueError("16-bit PGM not supported")
        for y in range(height):
            image.data[y] = [float(v) for v in raw[index:index + width]]
            index += width
    else:
        values = [float(v) * 255.0 / maxval for v in raw[index:].split()]
        image.data = [values[y * width:(y + 1) * width] for y in range(height)]
    return image


def write_pgm(path, image):
    """Write P5 binary PGM (8-bit, rounded and clamped)."""
    with open(path, "wb") as handle:
        handle.write(("P5\n%d %d\n255\n" % (image.width, image.height)).encode())
        for row in image.data:
            handle.write(bytes(min(max(int(round(v)), 0), 255) for v in row))
