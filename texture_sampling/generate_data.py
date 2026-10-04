"""生成采样结果数据：不同放大倍数 + 边界用例，写入 data/*.csv。

运行：python3 generate_data.py
仅依赖标准库。源纹理为 4x4，T(i,j) = i + 10j（如 T(3,2)=23）。
"""

import csv
import os

from texture_sampling import (
    REPEAT, CLAMP, MIRROR, WRAP_MODES,
    Texture, magnify_coordinates,
)

W = H = 4
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def source_texture():
    return Texture(W, H, [[i + 10 * j for i in range(W)] for j in range(H)])


def generate_magnification():
    """1x/2x/3x/4x 放大，最近邻与双线性，三种环绕，逐像素输出。"""
    path = os.path.join(DATA_DIR, "magnification_results.csv")
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["wrap_mode", "filter", "mag", "out_x", "out_y",
                         "u", "v", "value"])
        for mode in WRAP_MODES:
            for mag in (1, 2, 3, 4):
                tex = source_texture()
                us = magnify_coordinates(W * mag, W)
                vs = magnify_coordinates(H * mag, H)
                for oy, v in enumerate(vs):
                    for ox, u in enumerate(us):
                        nv = tex.sample_nearest(u, v, mode)
                        bv = tex.sample_bilinear(u, v, mode)
                        writer.writerow([mode, "nearest", "%dx" % mag,
                                         ox, oy, "%.6f" % u, "%.6f" % v,
                                         "%.6f" % nv])
                        writer.writerow([mode, "bilinear", "%dx" % mag,
                                         ox, oy, "%.6f" % u, "%.6f" % v,
                                         "%.6f" % bv])
    return path


# (label, u, v)：整数、负坐标、恰好等于边界、极大坐标
EDGE_CASES = [
    ("texel_center", 0.5, 0.5),
    ("texel_center_corner", 3.5, 3.5),
    ("integer_u", 1.0, 0.5),
    ("integer_uv", 1.0, 1.0),
    ("exact_boundary_u0", 0.0, 0.5),
    ("exact_boundary_un", 4.0, 0.5),
    ("exact_boundary_u2n", 8.0, 0.5),
    ("negative_center", -0.5, -0.5),
    ("negative_quarter", -0.25, -0.25),
    ("negative_integer", -1.0, -1.0),
    ("negative_far", -12.75, -12.25),
    ("beyond_n_quarter", 4.25, 0.5),
    ("beyond_n_half", 4.5, 0.5),
    ("huge_repeat_periodic", 10 ** 9, 10 ** 9 + 0.5),
    ("huge_negative", -(10 ** 9) - 0.5, 0.5),
]


def generate_edge_cases():
    path = os.path.join(DATA_DIR, "edge_cases.csv")
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["case", "wrap_mode", "u", "v",
                         "nearest_value", "bilinear_value"])
        for label, u, v in EDGE_CASES:
            for mode in WRAP_MODES:
                tex = source_texture()
                writer.writerow([
                    label, mode, repr(u), repr(v),
                    "%.6f" % tex.sample_nearest(u, v, mode),
                    "%.6f" % tex.sample_bilinear(u, v, mode),
                ])
    return path


def generate_wrap_index_table():
    """整数下标环绕对照表（n=4，x 从 -9 到 12），作为规则的明确数据。"""
    from texture_sampling import wrap_index
    path = os.path.join(DATA_DIR, "wrap_index_table.csv")
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["x", "repeat", "clamp", "mirror"])
        for x in range(-9, 13):
            writer.writerow([x] + [wrap_index(x, W, m) for m in WRAP_MODES])
    return path


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    paths = [generate_wrap_index_table(),
             generate_magnification(),
             generate_edge_cases()]

    # 打印一个直观摘要：2x 放大双线性结果矩阵（三种环绕）
    tex = source_texture()
    us = magnify_coordinates(2 * W, W)
    print("源纹理 4x4, T(i,j)=i+10j：")
    for row in source_texture()._data:
        print("  " + "  ".join("%4.0f" % v for v in row))
    for mode in WRAP_MODES:
        print("\n2x 放大双线性（%s），采样坐标 u/v: %s" %
              (mode, ["%.3f" % c for c in us]))
        for v in us:
            print("  " + "  ".join("%5.2f" % tex.sample_bilinear(u, v, mode)
                                   for u in us))
    print("\n已写出数据文件：")
    for p in paths:
        print("  " + p)


if __name__ == "__main__":
    main()
