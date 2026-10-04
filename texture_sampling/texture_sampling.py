"""纹理环绕（wrap）与双线性采样（bilinear sampling）库，仅使用 Python 3 标准库。

坐标与取值规则（明确规定，测试以此为准）
=========================================

1. 采样坐标 (u, v) 为连续浮点坐标，单位是 texel：
   texel (i, j) 的中心位于 (i + 0.5, j + 0.5)，
   texel (i, j) 覆盖连续区间 [i, i+1) x [j, j+1)。
   因此 u 恰好等于整数 k 时，落在 texel k 与 k-1 的公共边界上，
   双线性权重各为 0.5（两个方向同理）。

2. 环绕只作用于“整数 texel 下标”，不作用于连续坐标：
   双线性采样先取 floor(u)、floor(u)+1 两个整数下标，
   再分别用环绕规则映射到 [0, n) 内，绝不越界读取。

3. 三种环绕方式对整数下标 x、纹理尺寸 n 的规定（与 GPU 惯例一致）：

   - REPEAT（重复）:  w = x mod n
       负坐标按数学取模回绕，如 n=4: w(-1)=3, w(-4)=0。
   - CLAMP（钳制）:   w = min(max(x, 0), n-1)
       小于 0 钳到 0，大于等于 n 钳到 n-1。
   - MIRROR（镜像）:  周期为 2n 的镜像重复（同 OpenGL GL_MIRRORED_REPEAT），
       边界 texel 在反射点重复一次：
       先 r = x mod 2n，若 r >= n 则 w = 2n - 1 - r，否则 w = r。
       如 n=4: 下标序列 ... 0 1 2 3 | 3 2 1 0 | 0 1 2 3 | 3 2 1 0 ...
       （即 w(-1)=0, w(3)=3, w(4)=3, w(5)=2；反射轴在 x = n-0.5 与 x = -0.5。）

4. 双线性权重：令 s = u - 0.5（把坐标平移到 texel 中心基准），
   fx = s - floor(s)，fy 同理；
   结果 = (1-fx)(1-fy)T[x0,y0] + fx(1-fy)T[x1,y0]
        + (1-fx)fy T[x0,y1] + fx fy T[x1,y1]，
   其中 x1 = wrap(x0+1), y1 = wrap(y0+1)，按第 2 条保证不越界。
   推论：texel 中心 (i+0.5, j+0.5) 处采样值恰好等于该 texel；
   整数坐标处为相邻两 texel 的等权混合（接缝处最易出错，重点测试）。

5. 越界检测：Texture 每次读取前断言 0 <= i < n；
   计数器 out_of_bounds_reads 在任何采样后必须恒为 0（测试断言）。
"""

import math

REPEAT = "repeat"
CLAMP = "clamp"
MIRROR = "mirror"

WRAP_MODES = (REPEAT, CLAMP, MIRROR)


def wrap_index(x, n, mode):
    """把整数 texel 下标 x 按环绕规则映射到 [0, n)。x 必须为 int。"""
    if not isinstance(x, int):
        raise TypeError("wrap_index 只接受整数下标，收到 %r" % (x,))
    if n <= 0:
        raise ValueError("纹理尺寸必须为正，收到 %r" % (n,))
    if mode == REPEAT:
        return x % n
    if mode == CLAMP:
        if x < 0:
            return 0
        if x >= n:
            return n - 1
        return x
    if mode == MIRROR:
        r = x % (2 * n)
        return (2 * n - 1 - r) if r >= n else r
    raise ValueError("未知环绕方式: %r" % (mode,))


class Texture:
    """单通道浮点纹理，行主序存储，data[j][i] 为 texel (i, j)。"""

    def __init__(self, width, height, data):
        if width <= 0 or height <= 0:
            raise ValueError("纹理尺寸必须为正")
        if len(data) != height or any(len(row) != width for row in data):
            raise ValueError("数据形状与纹理尺寸不一致")
        self.width = width
        self.height = height
        self._data = [list(map(float, row)) for row in data]
        self.out_of_bounds_reads = 0  # 越界读取计数，任何采样后必须为 0

    def texel(self, i, j):
        """直接读取 texel (i, j)。越界时计数并抛错（正常路径不应触发）。"""
        if not (0 <= i < self.width and 0 <= j < self.height):
            self.out_of_bounds_reads += 1
            raise IndexError("越界读取 texel (%r, %r)，尺寸 %dx%d"
                             % (i, j, self.width, self.height))
        return self._data[j][i]

    def sample_nearest(self, u, v, mode_u=REPEAT, mode_v=None):
        """最近邻采样：floor 到 texel 下标后按环绕规则取值。"""
        if mode_v is None:
            mode_v = mode_u
        i = wrap_index(math.floor(u), self.width, mode_u)
        j = wrap_index(math.floor(v), self.height, mode_v)
        return self.texel(i, j)

    def sample_bilinear(self, u, v, mode_u=REPEAT, mode_v=None):
        """双线性采样。邻居下标按环绕规则映射，保证不越界读取。"""
        if mode_v is None:
            mode_v = mode_u
        su = u - 0.5
        sv = v - 0.5
        x0 = math.floor(su)
        y0 = math.floor(sv)
        fx = su - x0
        fy = sv - y0
        i0 = wrap_index(x0, self.width, mode_u)
        i1 = wrap_index(x0 + 1, self.width, mode_u)
        j0 = wrap_index(y0, self.height, mode_v)
        j1 = wrap_index(y0 + 1, self.height, mode_v)
        t00 = self.texel(i0, j0)
        t10 = self.texel(i1, j0)
        t01 = self.texel(i0, j1)
        t11 = self.texel(i1, j1)
        top = t00 + (t10 - t00) * fx
        bottom = t01 + (t11 - t01) * fx
        return top + (bottom - top) * fy


def magnify_coordinates(out_size, in_size):
    """放大 out_size/in_size 倍时，输出像素中心对应的输入坐标序列。

    映射规则：输出像素 p 的中心映射到输入坐标 (p + 0.5) * in_size / out_size，
    覆盖输入区间 [0, in_size)，mag=1 时输出像素中心恰好对齐 texel 中心。
    """
    scale = in_size / out_size
    return [(p + 0.5) * scale for p in range(out_size)]
