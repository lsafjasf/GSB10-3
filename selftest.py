"""selftest.py —— histeq 库的自测与报告生成。

运行：python3 selftest.py
  - 控制台打印测试结论（PASS/FAIL），失败时退出码非 0；
  - 生成 RESULTS.md：分布对比数据、接缝数值检查、边界用例结果。
"""

import random
import sys

import histeq

TILE = (8, 8)
REPORT = []  # 收集 Markdown 报告行


def log(line=""):
    print(line)
    REPORT.append(line)


# --------------------------------------------------------------------------
# 测试图像生成（全部确定性）
# --------------------------------------------------------------------------
def img_black(h=48, w=64):
    return [[0] * w for _ in range(h)]


def img_white(h=48, w=64):
    return [[255] * w for _ in range(h)]


def img_constant(v=128, h=48, w=64):
    return [[v] * w for _ in range(h)]


def img_normal(h=64, w=64):
    """对比度正常：对角渐变，灰度大致铺满 0~255。"""
    return [[(x * 255 // (w - 1) + y * 255 // (h - 1)) // 2
             for x in range(w)] for y in range(h)]


def img_dark(h=64, w=64):
    """整体偏暗（模拟欠曝光医学/遥感图）：灰度集中在 10~70。"""
    rng = random.Random(7)
    return [[10 + (x * 3 + y * 2) % 50 + rng.randint(0, 10)
             for x in range(w)] for y in range(h)]


def img_local(h=64, w=64):
    """局部对比不足：亮背景 + 中部暗区里藏弱纹理。"""
    rng = random.Random(11)
    img = []
    for y in range(h):
        row = []
        for x in range(w):
            if 16 <= y < 48 and 16 <= x < 48:
                row.append(40 + ((x * 5 + y * 3) % 12) + rng.randint(0, 4))
            else:
                row.append(200 + rng.randint(0, 20))
        img.append(row)
    return img


def img_gradient(h=64, w=64):
    """平滑渐变图：块状伪影在这类图上最典型，用于接缝数值检查。"""
    return [[(x * 2 + y) % 256 for x in range(w)] for y in range(h)]


def img_rgb(h=48, w=64):
    rng = random.Random(3)
    return [[[rng.randint(0, 255) for _ in range(3)]
             for _ in range(w)] for _ in range(h)]


# --------------------------------------------------------------------------
# 通用检查
# --------------------------------------------------------------------------
def check(cond, name, failures):
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond:
        failures.append(name)


def check_shape_and_range(before, after, tag, failures):
    check(histeq.shape(after) == histeq.shape(before),
          f"{tag}: 尺寸/通道数不变 {histeq.shape(before)}", failures)
    for chan in histeq.split_channels(after):
        flat = [v for row in chan for v in row]
        if not all(0 <= v <= 255 for v in flat):
            check(False, f"{tag}: 像素值保持在 0~255", failures)
            return
    check(True, f"{tag}: 像素值保持在 0~255", failures)


def stats_line(img):
    s = histeq.image_stats(img)[0]
    return (f"min={s['min']} max={s['max']} mean={s['mean']} "
            f"std={s['std']} median={s['median']} distinct={s['distinct']}")


def dist_table(rows):
    """rows: [(名称, 图像)]，输出 16 段灰度占比对比表（取第 1 通道）。"""
    _, edges = histeq.distribution(histeq.split_channels(rows[0][1])[0])
    labels = [f"{edges[k]}-{edges[k + 1]}" for k in range(len(edges) - 1)]
    lines = ["| 灰度段 | " + " | ".join(n for n, _ in rows) + " |",
             "|" + "---|" * (len(rows) + 1)]
    dists = [histeq.distribution(histeq.split_channels(img)[0])[0]
             for _, img in rows]
    for k, lab in enumerate(labels):
        lines.append("| " + lab + " | " +
                     " | ".join(f"{d[k]:.2f}%" for d in dists) + " |")
    return lines


# --------------------------------------------------------------------------
# 接缝数值检查
# --------------------------------------------------------------------------
def seam_metrics(out, by, bx):
    """返回 (接缝处平均梯度, 非接缝处平均梯度)。

    对每对相邻行/列计算平均绝对灰度差，按是否跨分块接缝分组。
    """
    h, w = len(out), len(out[0])
    seam, inner = [], []
    seams_y, seams_x = set(by[1:-1]), set(bx[1:-1])
    for y in range(1, h):
        m = sum(abs(out[y][x] - out[y - 1][x]) for x in range(w)) / w
        (seam if y in seams_y else inner).append(m)
    for x in range(1, w):
        m = sum(abs(out[y][x] - out[y][x - 1]) for y in range(h)) / h
        (seam if x in seams_x else inner).append(m)
    sm = sum(seam) / len(seam)
    im = sum(inner) / len(inner)
    return sm, im


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def main():
    failures = []

    log("# 直方图均衡 / 分块局部增强 —— 自测报告")
    log()
    log(f"分块参数 tile={TILE}（8x8 分块），局部增强默认开启块间双线性插值。")
    log()

    # ---- 1. 边界用例 ------------------------------------------------------
    log("## 1. 边界用例")
    log()
    cases = [
        ("全黑图像", img_black()),
        ("全白图像", img_white()),
        ("单值图像(128)", img_constant()),
        ("对比度正常图像", img_normal()),
        ("整体偏暗图像", img_dark()),
        ("局部对比不足图像", img_local()),
        ("RGB 彩色图像", img_rgb()),
    ]
    for name, img in cases:
        print(f"[用例] {name}")
        g = histeq.equalize_hist(img)
        l = histeq.local_equalize(img, TILE)
        check_shape_and_range(img, g, f"{name}/全局均衡", failures)
        check_shape_and_range(img, l, f"{name}/局部增强", failures)
        if name.startswith(("全黑", "全白", "单值")):
            check(g == img, f"{name}: 全局均衡后保持原值", failures)
            check(l == img, f"{name}: 局部增强后保持原值", failures)
        log(f"### {name}")
        log(f"- 处理前: {stats_line(img)}")
        log(f"- 全局均衡后: {stats_line(g)}")
        log(f"- 局部增强后: {stats_line(l)}")
        log()

    # ---- 2. 全局 vs 局部 分布对比 -----------------------------------------
    log("## 2. 全局均衡 vs 分块局部增强 —— 分布对比（16 段灰度占比）")
    log()
    for name, img in [("整体偏暗图像", img_dark()),
                      ("局部对比不足图像", img_local())]:
        g = histeq.equalize_hist(img)
        l = histeq.local_equalize(img, TILE)
        log(f"### {name}")
        log()
        log(f"- 原图: {stats_line(img)}")
        log(f"- 全局均衡: {stats_line(g)}")
        log(f"- 局部增强: {stats_line(l)}")
        log()
        for line in dist_table([("原图", img), ("全局均衡", g),
                                ("局部增强", l)]):
            log(line)
        log()
        if name.startswith("局部"):
            def region_std(im):
                reg = [row[16:48] for row in im[16:48]]
                return histeq.channel_stats(reg)["std"]
            s0, s1, s2 = region_std(img), region_std(g), region_std(l)
            log(f"中部暗区(32x32)灰度标准差: 原图={s0} 全局={s1} 局部={s2}")
            check(s2 > s0, "局部增强提升暗区局部对比度", failures)
            log()

    # ---- 3. 接缝数值检查 ---------------------------------------------------
    log("## 3. 分块接缝数值检查（平滑渐变图，blocky=不插值 vs interp=双线性插值）")
    log()
    img = img_gradient()
    by = histeq._tile_breaks(64, TILE[0])
    bx = histeq._tile_breaks(64, TILE[1])
    blocky = histeq.local_equalize(img, TILE, interpolate=False)
    interp = histeq.local_equalize(img, TILE, interpolate=True)
    sb, ib = seam_metrics(blocky, by, bx)
    si, ii = seam_metrics(interp, by, bx)
    log("平均绝对灰度差（相邻行/列，按是否跨分块接缝分组）：")
    log()
    log("| 指标 | blocky 不插值 | interp 双线性插值 |")
    log("|---|---|---|")
    log(f"| 接缝处平均梯度 | {sb:.2f} | {si:.2f} |")
    log(f"| 非接缝处平均梯度 | {ib:.2f} | {ii:.2f} |")
    log(f"| 接缝超额梯度(接缝-非接缝) | {sb - ib:.2f} | {si - ii:.2f} |")
    log()
    y = by[1]
    xm = 32
    log(f"接缝两侧像素数值抽样（水平接缝 y={y}，x={xm} 附近，"
        f"上=第{y - 1}行，下=第{y}行）：")
    log()
    log(f"- blocky: 上 {blocky[y - 1][xm - 2:xm + 2]} -> 下 {blocky[y][xm - 2:xm + 2]}"
        f"（跨缝突变 {abs(blocky[y][xm] - blocky[y - 1][xm])}）")
    log(f"- interp: 上 {interp[y - 1][xm - 2:xm + 2]} -> 下 {interp[y][xm - 2:xm + 2]}"
        f"（跨缝变化 {abs(interp[y][xm] - interp[y - 1][xm])}）")
    log()
    check(sb - ib > 50, "不插值时接缝处存在显著突变（复现方格接缝）", failures)
    check(si - ii < 5, "双线性插值后接缝超额梯度基本消除", failures)
    check(si < sb / 4, "插值后接缝平均梯度降至不插值的 1/4 以下", failures)

    # ---- 4. 双线性插值正确性 ----------------------------------------------
    log("## 4. 双线性插值正确性（解析验证）")
    log()
    # 4x4 图，tile=(2,2)：每块 2x2，块中心在 (1,1)/(1,3)/(3,1)/(3,3)。
    # 像素 (2,2) 位于四块中心正中，输出应为四个 LUT 值的平均。
    tiny = [[0, 10, 100, 110],
            [5, 15, 105, 115],
            [180, 190, 240, 250],
            [185, 195, 245, 255]]
    out = histeq.local_equalize(tiny, tile=(2, 2))
    luts, _, _ = histeq._grid_luts(tiny, 2, 2, None)
    v = tiny[2][2]
    lut4 = [luts[i][j][v] for i in range(2) for j in range(2)]
    expect = round(sum(lut4) / 4)
    got = out[2][2]
    log(f"- 4x4 图 tile=(2,2)，块中心在 (1,1)/(1,3)/(3,1)/(3,3)；")
    log(f"  像素(2,2)输入值 {v}，四块 LUT 映射值 {lut4}，"
        f"期望插值结果 {expect}，实际 {got}")
    check(abs(got - expect) <= 1, "四块中心正中像素 = 四 LUT 均值", failures)
    check(out[1][1] == luts[0][0][tiny[1][1]],
          "块中心像素 = 该块 LUT 映射值", failures)
    # 像素 (1,2)：位于上排两块中心之间，应为这两块 LUT 的均值
    v = tiny[1][2]
    expect2 = round((luts[0][0][v] + luts[0][1][v]) / 2)
    got2 = out[1][2]
    log(f"  像素(1,2)输入值 {v}，上排两块 LUT 映射值 "
        f"{[luts[0][0][v], luts[0][1][v]]}，期望 {expect2}，实际 {got2}")
    check(abs(got2 - expect2) <= 1, "两块中心之间像素 = 两 LUT 均值", failures)
    log()

    # ---- 5. 对比度受限（clip_limit） ---------------------------------------
    log("## 5. 对比度受限增强（clip_limit）")
    log()
    img = img_local()
    plain = histeq.local_equalize(img, TILE)
    clipped = histeq.local_equalize(img, TILE, clip_limit=4)
    sp = histeq.image_stats(plain)[0]["std"]
    sc = histeq.image_stats(clipped)[0]["std"]
    log(f"- 局部增强整体灰度标准差: 无限制={sp}，clip_limit=4 时={sc}")
    check(sc < sp, "clip_limit 限制增强幅度", failures)
    check_shape_and_range(img, clipped, "clip_limit/局部增强", failures)
    # 裁剪/重分配的守恒性与上限
    hist = histeq.histogram(histeq.split_channels(img)[0])
    total = sum(hist)
    rh = histeq._clip_redistribute(hist, total, 4)
    check(sum(rh) == total, "直方图裁剪重分配后像素总数守恒", failures)
    check(max(rh) < max(hist), "裁剪后直方图峰值被压低", failures)
    rh2 = histeq._clip_redistribute(hist, total, 1)  # 极小 clip 也能正常工作
    check(sum(rh2) == total, "clip_limit 极小时总数仍守恒", failures)
    log()

    # ---- 汇总 --------------------------------------------------------------
    log("## 结论")
    log()
    if failures:
        log(f"共 {len(failures)} 项失败：")
        for f in failures:
            log(f"- {f}")
    else:
        log("全部检查通过。")

    with open("RESULTS.md", "w", encoding="utf-8") as fp:
        fp.write("\n".join(REPORT) + "\n")

    print()
    print("报告已写入 RESULTS.md")
    if failures:
        print(f"SELFTEST FAILED: {len(failures)} 项")
        return 1
    print("SELFTEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
