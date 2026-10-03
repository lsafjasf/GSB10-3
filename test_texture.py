"""texture.py 的自测：环绕规则、边界取值、双线性采样、越界检测。

运行：python3 test_texture.py   （或 python3 -m unittest -v）

测试纹理为 4x4，texel(x, y) = y * 4 + x：
    0  1  2  3
    4  5  6  7
    8  9 10 11
   12 13 14 15
"""

import random
import unittest

from texture import Texture, WrapMode, wrap_index
from generate_sample_data import generate as generate_sample_data


def make_texture():
    return Texture(4, 4, [y * 4 + x for y in range(4) for x in range(4)])


class WrapRuleTest(unittest.TestCase):
    """三种环绕方式的映射表与边界规定。"""

    def test_repeat_table(self):
        expected = {i: i % 4 for i in range(-9, 10)}
        for i, want in expected.items():
            self.assertEqual(wrap_index(i, 4, WrapMode.REPEAT), want, "i=%d" % i)

    def test_clamp_table(self):
        for i in range(-9, 0):
            self.assertEqual(wrap_index(i, 4, WrapMode.CLAMP), 0, "i=%d" % i)
        for i in range(0, 4):
            self.assertEqual(wrap_index(i, 4, WrapMode.CLAMP), i, "i=%d" % i)
        for i in range(4, 10):
            self.assertEqual(wrap_index(i, 4, WrapMode.CLAMP), 3, "i=%d" % i)

    def test_mirror_table(self):
        # 周期 2n=8：0 1 2 3 3 2 1 0 | 0 1 2 3 3 2 1 0 ...
        # 负方向：-1->0, -2->1, -3->2, -4->3, -5->3, -6->2, -7->1, -8->0
        expected = {
            -9: 0, -8: 0, -7: 1, -6: 2, -5: 3, -4: 3, -3: 2, -2: 1, -1: 0,
            0: 0, 1: 1, 2: 2, 3: 3, 4: 3, 5: 2, 6: 1, 7: 0, 8: 0, 9: 1,
        }
        for i, want in expected.items():
            self.assertEqual(wrap_index(i, 4, WrapMode.MIRROR), want, "i=%d" % i)

    def test_mirror_identity(self):
        # 镜像的代数性质：i 与 -i-1 映射相同
        for n in (1, 2, 3, 4, 7):
            for i in range(-500, 500):
                self.assertEqual(wrap_index(i, n, WrapMode.MIRROR),
                                 wrap_index(-i - 1, n, WrapMode.MIRROR))

    def test_result_always_in_range(self):
        for n in range(1, 9):
            for mode in WrapMode:
                for i in range(-2000, 2001):
                    r = wrap_index(i, n, mode)
                    self.assertTrue(0 <= r < n, "n=%d i=%d mode=%s" % (n, i, mode))

    def test_invalid_size_rejected(self):
        for mode in WrapMode:
            self.assertRaises(ValueError, wrap_index, 0, 0, mode)
            self.assertRaises(ValueError, wrap_index, 0, -3, mode)


class OutOfBoundsDetectorTest(unittest.TestCase):
    """越界检测器本身必须能检出越界读取。"""

    def test_detector_counts_and_raises(self):
        tex = make_texture()
        for bad in ((-1, 0), (4, 0), (0, -1), (0, 4), (100, 100)):
            self.assertRaises(IndexError, tex.read, *bad)
        self.assertEqual(tex.out_of_bounds_reads, 5)
        self.assertEqual(tex.read(0, 0), 0)
        self.assertEqual(tex.read(3, 3), 15)
        self.assertEqual(tex.out_of_bounds_reads, 5)  # 合法读取不计数


class BilinearBoundaryTest(unittest.TestCase):
    """双线性采样在边界处按环绕规则取邻居。"""

    def setUp(self):
        self.tex = make_texture()

    def sample(self, u, v, mode):
        return self.tex.sample_bilinear(u, v, mode)

    def test_texel_center_exact(self):
        # 纹素中心 (2.5/4, 1.5/4)：三种方式都必须精确命中 texel(2,1)=6
        for mode in WrapMode:
            self.assertEqual(self.sample(0.625, 0.375, mode), 6.0)

    def test_midpoint_between_texels(self):
        # u=0.375 -> x=1.0 恰为整数，fx=0，取左侧纹素 texel(1,1)=5
        for mode in WrapMode:
            self.assertEqual(self.sample(0.375, 0.375, mode), 5.0)

    def test_corner_average(self):
        # 四个纹素正中间：mean(0, 1, 4, 5) = 2.5
        for mode in WrapMode:
            self.assertEqual(self.sample(0.25, 0.25, mode), 2.5)

    def test_integer_coordinate_zero(self):
        # u=0.0：x=-0.5，左邻居坐标 -1 按环绕规则映射
        self.assertEqual(self.sample(0.0, 0.375, WrapMode.REPEAT), 5.5)  # (7+4)/2
        self.assertEqual(self.sample(0.0, 0.375, WrapMode.CLAMP), 4.0)   # (4+4)/2
        self.assertEqual(self.sample(0.0, 0.375, WrapMode.MIRROR), 4.0)  # (4+4)/2

    def test_integer_coordinate_one(self):
        # u=1.0：x=3.5，右邻居坐标 4 按环绕规则映射
        self.assertEqual(self.sample(1.0, 0.375, WrapMode.REPEAT), 5.5)  # (7+4)/2
        self.assertEqual(self.sample(1.0, 0.375, WrapMode.CLAMP), 7.0)   # (7+7)/2
        self.assertEqual(self.sample(1.0, 0.375, WrapMode.MIRROR), 7.0)  # (7+7)/2

    def test_integer_coordinate_both_axes(self):
        # u=v=1.0：repeat 四角混合 (15+12+3+0)/4，clamp/mirror 吸附角点 15
        self.assertEqual(self.sample(1.0, 1.0, WrapMode.REPEAT), 7.5)
        self.assertEqual(self.sample(1.0, 1.0, WrapMode.CLAMP), 15.0)
        self.assertEqual(self.sample(1.0, 1.0, WrapMode.MIRROR), 15.0)

    def test_negative_integer_coordinate(self):
        # u=-1.0：x=-4.5，邻居坐标 -5, -4
        self.assertEqual(self.sample(-1.0, 0.375, WrapMode.REPEAT), 5.5)  # -5->3, -4->0
        self.assertEqual(self.sample(-1.0, 0.375, WrapMode.CLAMP), 4.0)   # 钳到第 0 列
        self.assertEqual(self.sample(-1.0, 0.375, WrapMode.MIRROR), 7.0)  # -5->3, -4->3

    def test_integer_coordinate_two(self):
        # u=2.0：x=7.5，邻居坐标 7, 8
        self.assertEqual(self.sample(2.0, 0.375, WrapMode.REPEAT), 5.5)  # 7->3, 8->0
        self.assertEqual(self.sample(2.0, 0.375, WrapMode.CLAMP), 7.0)
        self.assertEqual(self.sample(2.0, 0.375, WrapMode.MIRROR), 4.0)  # 7->0, 8->0

    def test_negative_fractional_coordinate(self):
        # u=-0.25：x=-1.5，邻居坐标 -2, -1
        self.assertEqual(self.sample(-0.25, 0.125, WrapMode.REPEAT), 2.5)  # (2+3)/2
        self.assertEqual(self.sample(-0.25, 0.125, WrapMode.CLAMP), 0.0)
        self.assertEqual(self.sample(-0.25, 0.125, WrapMode.MIRROR), 0.5)  # (1+0)/2

    def test_huge_coordinate_1e9(self):
        # u=1e9+0.125 -> x=4e9 恰为整数，fx=0
        self.assertEqual(self.sample(1e9 + 0.125, 0.125, WrapMode.REPEAT), 0.0)
        self.assertEqual(self.sample(1e9 + 0.125, 0.125, WrapMode.CLAMP), 3.0)
        self.assertEqual(self.sample(1e9 + 0.125, 0.125, WrapMode.MIRROR), 0.0)

    def test_huge_coordinate_1e12(self):
        # u=1e12 -> x=4e12-0.5，x0=3999999999999，fx=0.5
        self.assertEqual(self.sample(1e12, 0.125, WrapMode.REPEAT), 1.5)  # (3+0)/2
        self.assertEqual(self.sample(1e12, 0.125, WrapMode.CLAMP), 3.0)
        self.assertEqual(self.sample(1e12, 0.125, WrapMode.MIRROR), 0.0)

    def test_huge_negative_coordinate(self):
        # u=-1e9-0.375 -> x=-4e9-2.0，x0=-4e9-2，fx=0
        self.assertEqual(self.sample(-1e9 - 0.375, 0.125, WrapMode.REPEAT), 2.0)
        self.assertEqual(self.sample(-1e9 - 0.375, 0.125, WrapMode.CLAMP), 0.0)
        self.assertEqual(self.sample(-1e9 - 0.375, 0.125, WrapMode.MIRROR), 1.0)


class ZeroOutOfBoundsTest(unittest.TestCase):
    """任何坐标、任何环绕方式下采样，越界读取计数必须为零。"""

    def test_dense_sweep_zero_oob(self):
        tex = make_texture()
        coords = [0.0, 1.0, -1.0, 2.0, -2.0, 0.5, -0.5, 0.25, -0.25,
                  1e12, -1e12, 1e9 + 0.125, -1e9 - 0.375, 1e6, -1e6]
        coords += [-3.0 + 0.013 * k for k in range(462)]  # -3.0 .. 3.0 密集扫描
        for mode in WrapMode:
            for u in coords:
                for v in (0.0, 0.125, 0.375, 0.5, 0.999, 1.0, -0.75, 2.5):
                    tex.sample_bilinear(u, v, mode)
        self.assertEqual(tex.out_of_bounds_reads, 0)

    def test_random_fuzz_zero_oob(self):
        tex = make_texture()
        rng = random.Random(20261004)
        for _ in range(20000):
            u = rng.uniform(-1e6, 1e6)
            v = rng.uniform(-1e6, 1e6)
            mode = rng.choice(list(WrapMode))
            value = tex.sample_bilinear(u, v, mode)
            self.assertTrue(0.0 <= value <= 15.0)  # 双线性插值不超出纹素值域
        self.assertEqual(tex.out_of_bounds_reads, 0)


class SampleDataTest(unittest.TestCase):
    """采样结果数据：放大倍数 spot-check + 数据文件与代码一致。"""

    def test_magnification_spot_check(self):
        tex = make_texture()
        # 2x 放大、repeat、输出像素 (0,0)：u=v=0.0625 -> x=y=-0.25, fx=fy=0.75
        # 四角 (3,3)=15 (0,3)=12 (3,0)=3 (0,0)=0 -> 3.75
        self.assertAlmostEqual(tex.sample_bilinear(0.0625, 0.0625, WrapMode.REPEAT), 3.75)
        # 4x 放大、clamp、输出像素 (15,15)：u=v=15.5/16 -> 右下角，钳到 15
        self.assertAlmostEqual(tex.sample_bilinear(15.5 / 16, 15.5 / 16,
                                                   WrapMode.CLAMP), 15.0)
        # 8x 放大、mirror、输出像素 (0,0)：u=v=1/64 -> x=y=-0.21875
        # 镜像后邻居都在第 0 列/行：mean 加权 (0,1,4,5) -> 2.5*(1-t)^2... 直接核对数值
        self.assertAlmostEqual(tex.sample_bilinear(1 / 64, 1 / 64, WrapMode.MIRROR),
                               tex.sample_bilinear(1 / 64, 1 / 64, WrapMode.CLAMP))

    def test_sample_data_file_up_to_date(self):
        with open("sample_data.txt", "r", encoding="utf-8") as f:
            on_disk = f.read()
        self.assertEqual(on_disk, generate_sample_data(),
                         "sample_data.txt 已过期，请运行 python3 generate_sample_data.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)
