"""纹理环绕与采样自测：python3 -m unittest -v 或 python3 test_texture.py"""

import unittest

from texture_sampling import (
    REPEAT, CLAMP, MIRROR, WRAP_MODES,
    Texture, wrap_index, magnify_coordinates,
)

N = 4


def identity_tex():
    return Texture(N, N, [[i + 10 * j for i in range(N)] for j in range(N)])


class WrapIndexTest(unittest.TestCase):
    """环绕规则表测试（n=4），明确边界取值。"""

    def test_repeat_table(self):
        # ... -8 -7 -6 -5 | -4 -3 -2 -1 | 0 1 2 3 | 4 5 6 7 ...
        table = {
            -8: 0, -7: 1, -6: 2, -5: 3,
            -4: 0, -3: 1, -2: 2, -1: 3,
            0: 0, 1: 1, 2: 2, 3: 3,
            4: 0, 5: 1, 6: 2, 7: 3,
        }
        for x, expected in table.items():
            self.assertEqual(wrap_index(x, N, REPEAT), expected, "x=%d" % x)

    def test_clamp_table(self):
        table = {
            -99: 0, -1: 0, 0: 0, 1: 1, 2: 2, 3: 3,
            4: 3, 5: 3, 99: 3,
        }
        for x, expected in table.items():
            self.assertEqual(wrap_index(x, N, CLAMP), expected, "x=%d" % x)

    def test_clamp_n_minus_1_and_n(self):
        # 恰好等于边界：n-1 合法，n 被钳到 n-1
        self.assertEqual(wrap_index(N - 1, N, CLAMP), N - 1)
        self.assertEqual(wrap_index(N, N, CLAMP), N - 1)

    def test_mirror_table(self):
        # GL_MIRRORED_REPEAT：周期 2n=8，边界 texel 在反射点重复一次
        table = {
            -8: 0, -7: 1, -6: 2, -5: 3, -4: 3, -3: 2, -2: 1, -1: 0,
            0: 0, 1: 1, 2: 2, 3: 3,
            4: 3, 5: 2, 6: 1, 7: 0,
            8: 0, 9: 1,
        }
        for x, expected in table.items():
            self.assertEqual(wrap_index(x, N, MIRROR), expected, "x=%d" % x)

    def test_mirror_seam_reflection_point(self):
        # 反射点两侧都映射到 n-1：w(n-1)=w(n)=n-1，w(n+1)=n-2
        self.assertEqual(wrap_index(N - 1, N, MIRROR), N - 1)
        self.assertEqual(wrap_index(N, N, MIRROR), N - 1)
        self.assertEqual(wrap_index(N + 1, N, MIRROR), N - 2)
        self.assertEqual(wrap_index(-1, N, MIRROR), 0)

    def test_huge_coordinates(self):
        big = 10 ** 15
        self.assertEqual(wrap_index(big, N, REPEAT), big % N)
        self.assertEqual(wrap_index(-big, N, REPEAT), (-big) % N)
        self.assertEqual(wrap_index(big, N, CLAMP), N - 1)
        self.assertEqual(wrap_index(-big, N, CLAMP), 0)
        self.assertEqual(wrap_index(big, N, MIRROR), wrap_index(big % (2 * N), N, MIRROR))
        self.assertEqual(wrap_index(-big, N, MIRROR), wrap_index((-big) % (2 * N), N, MIRROR))

    def test_unknown_mode_and_bad_size(self):
        with self.assertRaises(ValueError):
            wrap_index(0, 0, REPEAT)
        with self.assertRaises(ValueError):
            wrap_index(0, N, "bogus")
        with self.assertRaises(TypeError):
            wrap_index(1.5, N, REPEAT)


class NearestTest(unittest.TestCase):
    def test_basic_and_wrapped(self):
        tex = identity_tex()
        self.assertEqual(tex.sample_nearest(0.5, 0.5, REPEAT), 0.0)
        self.assertEqual(tex.sample_nearest(3.5, 1.5, REPEAT), 13.0)
        self.assertEqual(tex.sample_nearest(-0.5, -0.5, REPEAT), 33.0)   # (-1,-1)->(3,3)
        self.assertEqual(tex.sample_nearest(4.5, 4.5, REPEAT), 0.0)      # 回绕到 (0,0)

    def test_clamp_edge(self):
        tex = identity_tex()
        self.assertEqual(tex.sample_nearest(-1.0, -1.0, CLAMP), 0.0)
        self.assertEqual(tex.sample_nearest(4.0, 4.0, CLAMP), 33.0)
        self.assertEqual(tex.sample_nearest(99.9, 99.9, CLAMP), 33.0)

    def test_mirror_seam(self):
        tex = identity_tex()
        # x=4.5 -> floor 4 -> 镜像 w(4)=3，最近邻取 texel 3
        self.assertEqual(tex.sample_nearest(4.5, 0.5, MIRROR), 3.0)
        # x=-0.5 -> floor -1 -> w(-1)=0
        self.assertEqual(tex.sample_nearest(-0.5, 0.5, MIRROR), 0.0)


class BilinearTest(unittest.TestCase):
    def test_integer_coordinate_is_midpoint(self):
        # u 恰好为整数：落在两 texel 边界，权重各 0.5
        tex = identity_tex()
        self.assertEqual(tex.sample_bilinear(1.0, 0.5, REPEAT), 0.5 * (0 + 1))
        self.assertEqual(tex.sample_bilinear(2.0, 0.5, REPEAT), 0.5 * (1 + 2))

    def test_integer_both_axes_quarter_mix(self):
        # (u,v) 都为整数：4 个 texel 各 1/4
        tex = identity_tex()
        expected = 0.25 * (0 + 1 + 10 + 11)
        self.assertEqual(tex.sample_bilinear(1.0, 1.0, REPEAT), expected)

    def test_center_of_texel_is_exact(self):
        # texel 中心 (i+0.5, j+0.5)：权重全落在该 texel 上，值必须精确
        tex = identity_tex()
        self.assertEqual(tex.sample_bilinear(0.5, 0.5, REPEAT), 0.0)
        self.assertEqual(tex.sample_bilinear(2.5, 1.5, REPEAT), 12.0)
        self.assertEqual(tex.sample_bilinear(3.5, 3.5, REPEAT), 33.0)

    def test_hand_computed_weights(self):
        # u=0.75 -> s=0.25, fx=0.25: 0.75*T0 + 0.25*T1（v 在 texel 中心）
        tex = identity_tex()
        self.assertEqual(tex.sample_bilinear(0.75, 0.5, REPEAT),
                         0.75 * 0 + 0.25 * 1)

    def test_negative_coordinates_repeat(self):
        # u=-0.5 -> s=-1.0, 恰好是 wrap(-1)=3 号 texel 的中心
        tex = identity_tex()
        self.assertEqual(tex.sample_bilinear(-0.5, 0.5, REPEAT), 3.0)
        # u=-0.25 -> s=-0.75, fx=0.25: 0.75*T3 + 0.25*T0
        self.assertEqual(tex.sample_bilinear(-0.25, 0.5, REPEAT),
                         0.75 * 3 + 0.25 * 0)

    def test_exact_boundary_u0_repeat_seam(self):
        # u=0 恰好边界：repeat 下邻居是 3 和 0（接缝两侧），各 0.5
        tex = identity_tex()
        self.assertEqual(tex.sample_bilinear(0.0, 0.5, REPEAT),
                         0.5 * (3 + 0))

    def test_exact_boundary_un_repeat_equals_u0(self):
        # 周期性：u=n 与 u=0 采样结果必须相同
        tex = identity_tex()
        for frac in (0.0, 0.25, 0.5, 0.75):
            a = tex.sample_bilinear(0.0 + frac, 2.3, REPEAT)
            b = tex.sample_bilinear(N + frac, 2.3, REPEAT)
            self.assertEqual(a, b, "frac=%r" % frac)

    def test_exact_boundary_clamp(self):
        # u=0 恰好边界：clamp 下邻居都被钳到 0（边缘被拉长，无接缝）
        tex = identity_tex()
        self.assertEqual(tex.sample_bilinear(0.0, 0.5, CLAMP), 0.0)
        # u=n=4：s=3.5, 邻居 wrap(3)=wrap(4)=3，即边缘 texel 重复采样
        self.assertEqual(tex.sample_bilinear(4.0, 0.5, CLAMP), 3.0)

    def test_clamp_far_outside_is_edge_texel(self):
        tex = identity_tex()
        # 极远处 floor 邻居都钳到 n-1，双线性退化为边缘 texel
        self.assertEqual(tex.sample_bilinear(1000.2, 1000.7, CLAMP), 33.0)
        self.assertEqual(tex.sample_bilinear(-1000.2, -1000.7, CLAMP), 0.0)

    def test_mirror_seam_at_u_n(self):
        # u=4.0：s=3.5, 邻居 wrap(3)=wrap(4)=3（反射点，边界 texel 重复）
        tex = identity_tex()
        self.assertEqual(tex.sample_bilinear(4.0, 0.5, MIRROR), 3.0)

    def test_mirror_symmetry(self):
        tex = identity_tex()
        # 反射轴在 u = n = 4.0：左右对称
        for delta in (0.25, 0.5, 1.3):
            self.assertEqual(
                tex.sample_bilinear(4.0 + delta, 0.5, MIRROR),
                tex.sample_bilinear(4.0 - delta, 0.5, MIRROR),
                "delta=%r" % delta)
        # 另一条反射轴在 u = 0.0
        for delta in (0.25, 0.5, 1.0):
            self.assertEqual(
                tex.sample_bilinear(0.0 + delta, 0.5, MIRROR),
                tex.sample_bilinear(0.0 - delta, 0.5, MIRROR),
                "delta=%r" % delta)

    def test_huge_coordinates(self):
        tex = identity_tex()
        big = 10 ** 9
        # repeat：极大坐标等于取模后的小坐标
        for frac in (0.0, 0.5):
            self.assertEqual(
                tex.sample_bilinear(big + frac, big + frac, REPEAT),
                tex.sample_bilinear(big % N + frac, big % N + frac, REPEAT))
        # mirror：极大坐标按 2n 周期折叠
        self.assertEqual(
            tex.sample_bilinear(big + 0.5, 0.5, MIRROR),
            tex.sample_bilinear(big % (2 * N) + 0.5, 0.5, MIRROR))
        # clamp：极大坐标退化为角点 texel
        self.assertEqual(tex.sample_bilinear(big + 0.9, big + 0.9, CLAMP), 33.0)

    def test_modes_give_different_results_at_seam(self):
        tex = identity_tex()
        # u=4.7 落在接缝外侧：s=4.2, fx=0.2
        # repeat 邻居 (0,1) -> 0.2; clamp 邻居 (3,3) -> 3.0; mirror 邻居 (3,2) -> 2.8
        results = {m: tex.sample_bilinear(4.7, 0.5, m) for m in WRAP_MODES}
        self.assertAlmostEqual(results[REPEAT], 0.2)
        self.assertAlmostEqual(results[CLAMP], 3.0)
        self.assertAlmostEqual(results[MIRROR], 2.8)
        self.assertNotEqual(results[REPEAT], results[CLAMP])
        self.assertNotEqual(results[REPEAT], results[MIRROR])
        self.assertNotEqual(results[CLAMP], results[MIRROR])


class BoundsGuardTest(unittest.TestCase):
    def test_no_out_of_bounds_reads_under_all_samplings(self):
        """核心断言：任意坐标、任意环绕方式采样后越界读取计数必须为 0。"""
        tex = identity_tex()
        coords = [
            0.0, 0.5, 1.0, 3.0, 3.5, 4.0, 4.5, N, N + 0.5,
            -0.0, -0.5, -1.0, -3.7, -1000.25, 1000.75, 10 ** 12 + 0.3,
        ]
        for sampler in (tex.sample_nearest, tex.sample_bilinear):
            for u in coords:
                for v in coords:
                    for mode in WRAP_MODES:
                        sampler(u, v, mode)
        self.assertEqual(tex.out_of_bounds_reads, 0,
                         "检测到越界读取：%d 次" % tex.out_of_bounds_reads)

    def test_guard_actually_detects_direct_bad_access(self):
        # 证明计数器与守卫本身有效：直接越界访问必须被抓到
        tex = identity_tex()
        with self.assertRaises(IndexError):
            tex.texel(N, 0)
        with self.assertRaises(IndexError):
            tex.texel(0, -1)
        self.assertEqual(tex.out_of_bounds_reads, 2)


class MagnifyTest(unittest.TestCase):
    def test_coordinate_map(self):
        self.assertEqual(magnify_coordinates(N, N), [0.5, 1.5, 2.5, 3.5])
        self.assertEqual(magnify_coordinates(8, N)[0], 0.25)
        self.assertEqual(magnify_coordinates(8, N)[-1], 3.75)
        self.assertEqual(magnify_coordinates(2, 4), [0.25 * 4, 0.75 * 4])


if __name__ == "__main__":
    unittest.main(verbosity=2)
