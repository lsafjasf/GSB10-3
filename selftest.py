# -*- coding: utf-8 -*-
"""selftest.py — histeq 库的自测与对比数据生成（仅标准库）

运行：python3 selftest.py
内容：
  1. 边界用例：全黑 / 全白 / 单值 / 对比度正常 / 整体偏暗 / 局部对比不足
  2. 全局均衡 vs 分块局部增强 的分布与对比度对比数据
  3. 接缝数值检查：块间双线性插值 vs 不插值对照组
  4. 尺寸与通道数不变性断言（含 3 通道彩色图）
"""

import math
import histeq as he

W, H = 128, 96
TILES = 8
FAILURES = []


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print("  [%s] %s %s" % (tag, name, detail))
    if not cond:
        FAILURES.append(name)


def fmt_stats(st):
    return "min=%3d max=%3d mean=%6.2f std=%5.2f" % (
        st["min"], st["max"], st["mean"], st["std"])


def hist_line(plane, bins=16):
    """把 256 级直方图压成 bins 段，便于打印分布。"""
    h = he.histogram(plane)
    step = 256 // bins
    return "[" + " ".join("%5d" % sum(h[i * step:(i + 1) * step])
                          for i in range(bins)) + "]"


# ---------------------------------------------------------------- 测试图像

def img_black():
    return [[0] * W for _ in range(H)]

def img_white():
    return [[255] * W for _ in range(H)]

def img_flat():
    return [[128] * W for _ in range(H)]

def img_normal():
    """对比度正常：全量程渐变 + 纹理。"""
    return [[(x * 255 // (W - 1) +
              int(40 * math.sin(x * 0.35) * math.sin(y * 0.3))) % 256
             for x in range(W)] for y in range(H)]

def img_dark():
    """整体偏暗（模拟曝光不足的医学/遥感影像）：值域 0..55。"""
    return [[(x * 55 // (W - 1) + int(8 * math.sin(x * 0.4 + y * 0.2))) % 56
             for x in range(W)] for y in range(H)]

def img_low_local():
    """局部对比不足：亮背景中一块暗淡低对比区域（模拟局部病灶/云下阴影）。"""
    img = [[200] * W for _ in range(H)]
    for y in range(H // 4, 3 * H // 4):
        for x in range(W // 4, 3 * W // 4):
            img[y][x] = 30 + int(12 * math.sin(x * 0.5) * math.cos(y * 0.4))
    return img

def img_color():
    """3 通道彩色图，用于通道数不变性检查。"""
    return [[(x * 255 // (W - 1), y * 255 // (H - 1), 128)
             for x in range(W)] for y in range(H)]


# ---------------------------------------------------------------- 1. 边界用例

def test_edge_cases():
    print("=" * 72)
    print("1. 边界用例（全黑 / 全白 / 单值 / 正常对比度）")
    print("=" * 72)
    cases = [("全黑", img_black()), ("全白", img_white()),
             ("单值128", img_flat()), ("正常对比度", img_normal())]
    for name, img in cases:
        g = he.equalize_global(img)
        l = he.equalize_local(img, tiles=TILES)
        sg, sl = he.stats(g), he.stats(l)
        same_g = g == img
        # 局部增强对单值图应近似恒等（容差 ±1，防浮点取整抖动）
        flat_ok = all(abs(l[y][x] - img[y][x]) <= 1
                      for y in range(0, H, 7) for x in range(0, W, 7))
        print("  [%s] 原图      %s" % (name, fmt_stats(he.stats(img))))
        print("  [%s] 全局均衡  %s" % (name, fmt_stats(sg)))
        print("  [%s] 局部增强  %s" % (name, fmt_stats(sl)))
        if name in ("全黑", "全白", "单值128"):
            check(name + "-全局恒等", same_g)
            check(name + "-局部近似恒等", flat_ok)
        else:
            check(name + "-全局后std增大", sg["std"] > he.stats(img)["std"])
        check(name + "-尺寸不变",
              he.image_shape(g) == (H, W, 1) and he.image_shape(l) == (H, W, 1))


# ---------------------------------------------------------------- 2. 全局 vs 局部 对比数据

def test_compare():
    print()
    print("=" * 72)
    print("2. 全局均衡 vs 分块局部增强：分布与对比度对比（偏暗图 / 局部低对比图）")
    print("=" * 72)
    for name, img in [("整体偏暗", img_dark()), ("局部低对比", img_low_local())]:
        g = he.equalize_global(img)
        l = he.equalize_local(img, tiles=TILES)
        print("  --- %s ---" % name)
        print("  原图      : %s  局部对比度=%.2f" % (fmt_stats(he.stats(img)),
                                               he.local_contrast(img)))
        print("  全局均衡  : %s  局部对比度=%.2f" % (fmt_stats(he.stats(g)),
                                                 he.local_contrast(g)))
        print("  局部增强  : %s  局部对比度=%.2f" % (fmt_stats(he.stats(l)),
                                                 he.local_contrast(l)))
        print("  直方图(16段) 原图: %s" % hist_line(img))
        print("  直方图(16段) 全局: %s" % hist_line(g))
        print("  直方图(16段) 局部: %s" % hist_line(l))
        lc0, lc1 = he.local_contrast(img), he.local_contrast(l)
        check(name + "-局部增强提升局部对比度", lc1 > lc0,
              "(%.2f -> %.2f)" % (lc0, lc1))
        if name == "局部低对比":
            # 全局拉伸对局部低对比区域提升有限，局部增强应明显更好
            check(name + "-局部优于全局",
                  he.local_contrast(l) > he.local_contrast(g),
                  "(%.2f vs %.2f)" % (he.local_contrast(l), he.local_contrast(g)))


# ---------------------------------------------------------------- 3. 接缝数值检查

def seam_profile(plane, tiles, axis="x"):
    """返回 (接缝处平均跳变, 块内部平均跳变)。
    axis='x'：垂直接缝（沿 x 切分），计算水平相邻像素差；
    axis='y'：水平接缝（沿 y 切分），计算垂直相邻像素差。
    """
    h, w = len(plane), len(plane[0])
    length = w if axis == "x" else h
    seams = {round(j * length / tiles) for j in range(1, tiles)}
    seam_d, seam_n, inner_d, inner_n = 0.0, 0, 0.0, 0
    for y in range(h):
        for x in range(w):
            if axis == "x":
                if x == 0:
                    continue
                pos, py, px = x, y, x - 1
            else:
                if y == 0:
                    continue
                pos, py, px = y, y - 1, x
            d = abs(plane[y][x] - plane[py][px])
            if pos in seams:
                seam_d += d
                seam_n += 1
            else:
                inner_d += d
                inner_n += 1
    return seam_d / seam_n, inner_d / inner_n


def test_seams():
    print()
    print("=" * 72)
    print("3. 接缝数值检查（块间双线性插值 vs 不插值对照组，tiles=%d）" % TILES)
    print("=" * 72)
    # 使用平滑无突边的偏暗渐变图：输入本身在接缝处没有边缘，
    # 因此输出在接缝处的跳变可完全归因于分块映射的不连续。
    img = img_dark()
    interp = he.equalize_local(img, tiles=TILES, interpolate=True)
    nointerp = he.equalize_local(img, tiles=TILES, interpolate=False)
    s_0, n_0 = seam_profile(img, TILES)
    s_i, n_i = seam_profile(interp, TILES)
    s_n, n_n = seam_profile(nointerp, TILES)
    hs_0, hn_0 = seam_profile(img, TILES, axis="y")
    hs_i, hn_i = seam_profile(interp, TILES, axis="y")
    hs_n, hn_n = seam_profile(nointerp, TILES, axis="y")
    print("  原始输入    : 接缝处平均跳变=%.3f  块内部平均跳变=%.3f"
          % (s_0, n_0))
    print("  不插值对照组: 接缝处平均跳变=%.3f  块内部平均跳变=%.3f  比值=%.2f"
          % (s_n, n_n, s_n / n_n))
    print("  双线性插值组: 接缝处平均跳变=%.3f  块内部平均跳变=%.3f  比值=%.2f"
          % (s_i, n_i, s_i / n_i))
    # 超额跳变 = 输出跳变 - 输入跳变（剔除图像自身内容边缘的贡献）
    ex_n, ex_i = s_n - s_0, s_i - s_0
    print("  接缝处超额跳变(输出-输入): 不插值=%.3f  插值=%.3f" % (ex_n, ex_i))
    print("  [水平接缝] 输入=%.3f  插值=%.3f(内部%.3f)  不插值=%.3f(内部%.3f)"
          % (hs_0, hs_i, hn_i, hs_n, hn_n))
    check("垂直接缝-插值后跳变与块内部相当(比值<1.5)", s_i / n_i < 1.5,
          "比值=%.2f" % (s_i / n_i))
    check("水平接缝-插值后跳变与块内部相当(比值<1.5)", hs_i / hn_i < 1.5,
          "比值=%.2f" % (hs_i / hn_i))
    check("垂直接缝-插值显著降低超额跳变", ex_i < 0.5 * ex_n,
          "(%.3f vs %.3f)" % (ex_i, ex_n))
    # 该测试图沿 y 方向变化平缓，块间映射差异本就较小，
    # 因此水平方向断言插值严格优于不插值即可。
    check("水平接缝-插值严格优于不插值",
          hs_i - hs_0 < hs_n - hs_0,
          "(%.3f vs %.3f)" % (hs_i - hs_0, hs_n - hs_0))

    # 逐接缝明细
    w = W
    print("  各垂直接缝 x 处 |f(x)-f(x-1)| 均值（输入 / 插值 / 不插值）:")
    for j in range(1, TILES):
        x = round(j * w / TILES)
        d0 = sum(abs(img[y][x] - img[y][x - 1]) for y in range(H)) / H
        di = sum(abs(interp[y][x] - interp[y][x - 1]) for y in range(H)) / H
        dn = sum(abs(nointerp[y][x] - nointerp[y][x - 1]) for y in range(H)) / H
        print("    seam x=%3d : %6.3f / %6.3f / %6.3f" % (x, d0, di, dn))


# ---------------------------------------------------------------- 4. 尺寸/通道不变性

def test_shape():
    print()
    print("=" * 72)
    print("4. 尺寸与通道数不变性（灰度 + 3 通道彩色）")
    print("=" * 72)
    gray = img_normal()
    color = img_color()
    for name, img, expect in [("灰度", gray, (H, W, 1)),
                              ("彩色3通道", color, (H, W, 3))]:
        g = he.equalize_global(img)
        l = he.equalize_local(img, tiles=TILES)
        check(name + "-全局形状不变", he.image_shape(g) == expect,
              str(he.image_shape(g)))
        check(name + "-局部形状不变", he.image_shape(l) == expect,
              str(he.image_shape(l)))
    # 非整除尺寸
    odd = [[(x * 3 + y * 5) % 256 for x in range(101)] for y in range(77)]
    check("奇数尺寸101x77-局部形状不变",
          he.image_shape(he.equalize_local(odd, tiles=8)) == (77, 101, 1))
    check("奇数尺寸101x77-全局形状不变",
          he.image_shape(he.equalize_global(odd)) == (77, 101, 1))
    # 小图（尺寸小于分块数）
    tiny = [[(x + y) % 256 for x in range(5)] for y in range(4)]
    check("小图4x5-局部形状不变",
          he.image_shape(he.equalize_local(tiny, tiles=8)) == (4, 5, 1))


# ---------------------------------------------------------------- main

def main():
    test_edge_cases()
    test_compare()
    test_seams()
    test_shape()
    print()
    print("=" * 72)
    if FAILURES:
        print("结果：%d 项失败 -> %s" % (len(FAILURES), FAILURES))
        raise SystemExit(1)
    print("结果：全部通过 ✔")


if __name__ == "__main__":
    main()
