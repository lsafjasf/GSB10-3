"""纹理环绕（wrap）与双线性采样（bilinear sampling）库。仅使用标准库。

坐标约定
--------
- 纹理尺寸 W x H，纹素按行主序存储，整数坐标 (x, y)，0 <= x < W，0 <= y < H。
- 采样使用归一化 UV 坐标，纹素中心位于 ((i + 0.5) / n)。
  即 u = 0.5 / W 命中第 0 列中心，u = 1.0 落在最后一列与“下一列”的正中间。
- 双线性采样：x = u * W - 0.5，x0 = floor(x)，fx = x - x0；
  四个邻居 (x0, y0) (x0+1, y0) (x0, y0+1) (x0+1, y0+1) 的整数坐标
  一律先经 wrap_index 按环绕规则映射回 [0, n-1] 再读取，绝不越界。

环绕规则（对整数纹素坐标 i，轴向尺寸 n）的明确规定
-------------------------------------------------
- REPEAT（重复）：r = i mod n（数学取模，结果恒在 [0, n-1]）。
  负坐标向负方向周期延拓：i = -1 -> n-1，i = -n -> 0。
- CLAMP（钳制）：i < 0 取 0，i > n-1 取 n-1，其余不变。
  边界外所有坐标都吸附到边缘纹素。
- MIRROR（镜像）：以 2n 为周期镜像。令 t = i mod 2n，
  t < n 时 r = t，否则 r = 2n - 1 - t。
  等价性质：i 与 -i-1 映射相同（-1 -> 0，-2 -> 1），
  i = n -> n-1，i = n+1 -> n-2。边缘纹素只出现一次，不重复。

边界取值规定
------------
- u 恰好为整数（0、±1、±2 ...）时，x = u*n - 0.5 的小数部分恒为 0.5，
  即采样点落在两列正中间，两邻居各贡献 50%。
- u = 1.0 时右邻居整数坐标为 n：REPEAT 取第 0 列，CLAMP/MIRROR 取第 n-1 列。
- u = 0.0 时左邻居整数坐标为 -1：REPEAT 取第 n-1 列，CLAMP/MIRROR 取第 0 列。
- 坐标有效范围：|u| * n < 2**52（双精度安全整数范围），
  覆盖 ±1e12 量级的“极大坐标”。
"""

import math
from enum import Enum


class WrapMode(Enum):
    REPEAT = "repeat"
    CLAMP = "clamp"
    MIRROR = "mirror"


def wrap_index(i, n, mode):
    """把整数纹素坐标 i 按环绕方式 mode 映射到 [0, n-1]。

    返回值保证满足 0 <= r < n（带断言自检）。
    """
    if n <= 0:
        raise ValueError("size n must be positive, got %r" % (n,))
    if not isinstance(mode, WrapMode):
        raise ValueError("unknown wrap mode: %r" % (mode,))
    i = int(i)
    if mode is WrapMode.REPEAT:
        r = i % n  # Python 取模结果符号跟除数一致，恒为非负
    elif mode is WrapMode.CLAMP:
        if i < 0:
            r = 0
        elif i > n - 1:
            r = n - 1
        else:
            r = i
    else:  # WrapMode.MIRROR
        t = i % (2 * n)
        r = t if t < n else 2 * n - 1 - t
    assert 0 <= r < n, "wrap_index produced out-of-range index"
    return r


class Texture:
    """二维纹理，支持越界读取检测与双线性采样。"""

    def __init__(self, width, height, pixels):
        if width <= 0 or height <= 0:
            raise ValueError("texture size must be positive")
        pixels = list(pixels)
        if len(pixels) != width * height:
            raise ValueError("pixel count %d != %d x %d"
                             % (len(pixels), width, height))
        self.width = width
        self.height = height
        self.pixels = pixels
        # 越界读取计数器：任何未经环绕映射的越界访问都会累加并抛异常。
        self.out_of_bounds_reads = 0

    def read(self, x, y):
        """按整数坐标读取纹素。越界时计数并抛 IndexError（越界检测）。"""
        if not (0 <= x < self.width and 0 <= y < self.height):
            self.out_of_bounds_reads += 1
            raise IndexError("texel read out of bounds: (%d, %d) not in %dx%d"
                             % (x, y, self.width, self.height))
        return self.pixels[y * self.width + x]

    def read_wrapped(self, x, y, mode_x, mode_y):
        """先按环绕规则映射整数坐标，再读取。保证不越界。"""
        return self.read(wrap_index(x, self.width, mode_x),
                         wrap_index(y, self.height, mode_y))

    def sample_bilinear(self, u, v, mode_x=WrapMode.REPEAT, mode_y=None):
        """归一化坐标 (u, v) 双线性采样，边界邻居按环绕规则取得。"""
        if mode_y is None:
            mode_y = mode_x
        x = u * self.width - 0.5
        y = v * self.height - 0.5
        x0 = math.floor(x)
        y0 = math.floor(y)
        fx = x - x0
        fy = y - y0
        c00 = self.read_wrapped(x0, y0, mode_x, mode_y)
        c10 = self.read_wrapped(x0 + 1, y0, mode_x, mode_y)
        c01 = self.read_wrapped(x0, y0 + 1, mode_x, mode_y)
        c11 = self.read_wrapped(x0 + 1, y0 + 1, mode_x, mode_y)
        top = c00 * (1.0 - fx) + c10 * fx
        bottom = c01 * (1.0 - fx) + c11 * fx
        return top * (1.0 - fy) + bottom * fy
