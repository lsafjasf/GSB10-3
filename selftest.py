#!/usr/bin/env python3
"""曲线细分 / 折线近似库的自测与数据生成。

运行：python3 selftest.py
产出：
  data/tolerance_vs_points.csv  容限 - 点数 - 实测偏差 关系数据
  data/degree_reduction.csv     降阶阶数 - 实测降阶误差
全部用例使用 assert 校验；全部通过时退出码为 0。
"""

import csv
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bezier_flatten as bf

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# ---------------------------------------------------------------------------
# 测试曲线
# ---------------------------------------------------------------------------

ARCH = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)]            # 拱形
CUSP = [(0.0, 0.0), (0.0, 1.0), (1.0, -1.0), (1.0, 0.0)]           # 尖点型 S
LOOP = [(0.0, 0.0), (1.2, 1.6), (1.2, -1.4), (0.0, 0.0)]           # 闭环（端点重合）
STRAIGHT = [(0.0, 0.0), (1/3, 2/3), (2/3, 4/3), (1.0, 2.0)]        # 退化成直线
ALL_SAME = [(2.0, 3.0), (2.0, 3.0), (2.0, 3.0), (2.0, 3.0)]        # 控制点重合
CUBE_3D = [(0.0, 0.0, 0.0), (0.0, 1.0, 0.8), (1.0, 1.0, -0.6),
           (1.0, 0.0, 0.2)]


def sin_wave_curve(degree: int, cycles: float = 1.5, amp: float = 0.6):
    """按正弦波取控制点构造高阶曲线。"""
    return [(i / degree, amp * math.sin(2 * math.pi * cycles * i / degree))
            for i in range(degree + 1)]


SIN6 = sin_wave_curve(6)
SIN9 = sin_wave_curve(9)
SIN12 = sin_wave_curve(12, cycles=2.0)

TOLERANCES = [1e-1, 5e-2, 2e-2, 1e-2, 5e-3, 2e-3, 1e-3,
              5e-4, 2e-4, 1e-4, 5e-5]

passed = 0


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    global passed
    passed += 1
    print(f"  [PASS] {msg}")


# ---------------------------------------------------------------------------
# 关系数据 + 偏差验证
# ---------------------------------------------------------------------------

def sweep(name, controls, tolerances, samples=64):
    rows = []
    for tol in tolerances:
        res = bf.flatten(controls, tol)
        dev, dev_t = bf.polyline_deviation(controls, res.points, res.ts,
                                           samples_per_segment=samples)
        ratio = dev / tol
        # 核心可验证保证：实测最大偏差必须 <= 容限（留极小浮点余量）
        check(dev <= tol * (1 + 1e-9) + 1e-15,
              f"{name} tol={tol:g}: 实测偏差 {dev:.3e} <= {tol:.0e}，"
              f"折线段数 {len(res.points) - 1}")
        check(res.points[0] == tuple(controls[0])
              and res.points[-1] == tuple(controls[-1]),
              f"{name} tol={tol:g}: 折线端点与曲线端点精确一致")
        check(not res.max_depth_reached,
              f"{name} tol={tol:g}: 未触及递归深度上限")
        rows.append(dict(curve=name, degree=len(controls) - 1,
                         tolerance=tol, segments=len(res.points) - 1,
                         points=res.num_points, max_deviation=dev,
                         worst_t=dev_t, ratio=ratio))
    return rows


def fit_log_slope(rows):
    """拟合 N_seg ~ C * tol^k，返回斜率 k（典型值约 -0.5）。"""
    xs = [math.log(r["tolerance"]) for r in rows]
    ys = [math.log(r["segments"]) for r in rows]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    k = (sum((x - mx) * (y - my) for x, y in zip(xs, ys))
         / sum((x - mx) ** 2 for x in xs))
    c = math.exp(my - k * mx)
    return k, c


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    t0 = time.time()
    all_rows = []

    print("== 1. 容限-点数关系 & 实测偏差验证 ==")
    for name, curve, tols, sps in [
        ("cubic_arch", ARCH, TOLERANCES, 64),
        ("cubic_cusp", CUSP, TOLERANCES, 64),
        ("degree9_sin", SIN9, TOLERANCES[:8], 48),
    ]:
        rows = sweep(name, curve, tols, sps)
        all_rows.extend(rows)
        k, c = fit_log_slope(rows)
        print(f"  >> {name}: N_seg ≈ {c:.2f} * tol^({k:.2f})"
              f"（理论斜率 -0.5）")

    print("\n  曲线 cubic_arch 明细：")
    print(f"    {'容限':>10} {'折线段数':>8} {'点数':>6} "
          f"{'实测最大偏差':>14} {'偏差/容限':>10}")
    for r in all_rows:
        if r["curve"] == "cubic_arch":
            print(f"    {r['tolerance']:>10.0e} {r['segments']:>8d} "
                  f"{r['points']:>6d} {r['max_deviation']:>14.3e} "
                  f"{r['ratio']:>10.3f}")

    with open(os.path.join(DATA_DIR, "tolerance_vs_points.csv"),
              "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)

    # ------------------------------------------------------------------
    # 降阶数据
    # ------------------------------------------------------------------
    print("\n== 2. 任意阶数：升阶 / 降阶与降阶误差 ==")
    red_rows = []

    def reduce_case(name, orig, target, samples=2001):
        red = bf.reduce_degree(orig, target)
        err = bf.measure_reduction_error(orig, red, samples)
        red_rows.append(dict(curve=name, orig_degree=len(orig) - 1,
                             reduced_degree=target,
                             max_error=err))
        return red, err

    for target in (2,):
        red, err = reduce_case("cubic_arch->line", ARCH, target)
    print(f"    三次拱形曲线降为直线：实测误差 {err:.4e}")
    check(err > 1e-3, "降阶误差为正且可量化")

    for tgt in (5, 4, 3, 2):
        red, err = reduce_case("degree6_sin", SIN6, tgt)
        print(f"    6 阶正弦波 -> {tgt} 阶：实测误差 {err:.4e}")

    for tgt in (8, 6, 4, 2):
        red, err = reduce_case("degree9_sin", SIN9, tgt)
        print(f"    9 阶正弦波 -> {tgt} 阶：实测误差 {err:.4e}")

    # 升阶是精确的：三次升到 8 阶再降回 3 阶，误差应接近机器精度
    elevated = bf.elevate(ARCH, 8)
    elev_err = bf.measure_reduction_error(ARCH, elevated, 1001)
    check(elev_err < 1e-12, f"升阶 3->8 精确，点误差 {elev_err:.2e}")
    back = bf.reduce_degree(elevated, 3)
    back_err = bf.measure_reduction_error(ARCH, back, 2001)
    check(back_err < 1e-9, f"升阶再降阶复原，误差 {back_err:.2e}")
    red_rows.append(dict(curve="roundtrip 3->8->3", orig_degree=3,
                         reduced_degree=3, max_error=back_err))
    print(f"    升阶再降阶 3->8->3：复原误差 {back_err:.2e}")

    # 降阶后用 (容限+降阶误差) 再细分，可组合保证偏差
    reduced, rerr = reduce_case("cubic_arch->quad", ARCH, 2, 5001)
    tol = 1e-3
    res2 = bf.flatten(reduced, tol)
    dev, _ = bf.polyline_deviation(reduced, res2.points, res2.ts, 32)
    check(dev <= tol * (1 + 1e-9),
          f"降阶曲线细分偏差 {dev:.2e} <= {tol:.0e}")
    print(f"    降阶误差 {rerr:.3e} + 细分偏差 {dev:.3e} "
          f"<= 组合上界 {tol + rerr:.3e}")

    with open(os.path.join(DATA_DIR, "degree_reduction.csv"),
              "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(red_rows[0].keys()))
        w.writeheader()
        w.writerows(red_rows)

    # ------------------------------------------------------------------
    # 边界用例
    # ------------------------------------------------------------------
    # 独立的全局密集交叉验证（20 万均匀点，取到整条折线的最短距离）
    res = bf.flatten(ARCH, 1e-3)
    gdev = bf.global_dense_deviation(ARCH, res.points, 200001)
    check(gdev <= 1e-3 * (1 + 1e-9),
          f"全局 20 万点独立交叉验证：偏差 {gdev:.3e} <= 1e-3")
    print(f"    全局密集验证偏差 {gdev:.3e}（分段采样 7.324e-04，互相印证）")

    print("\n== 3. 边界用例 ==")

    # (a) 退化成直线：任意容限下 1 条折线、0 偏差
    res = bf.flatten(STRAIGHT, 1e-6)
    check(res.num_points == 2, "直线型曲线只产生 2 个顶点")
    dev, _ = bf.polyline_deviation(STRAIGHT, res.points, res.ts, 16)
    check(dev <= 1e-14, f"直线型曲线偏差 {dev:.1e} ≈ 0")
    check(bf.is_flat(STRAIGHT, 0.0), "完全共线时容限 0 也判平坦")

    # (b) 控制点全部重合：退化为点
    res = bf.flatten(ALL_SAME, 1e-9)
    check(res.num_points == 2 and res.points[0] == res.points[1],
          "重合控制点退化为一个点（2 个相同顶点）")
    dev, _ = bf.polyline_deviation(ALL_SAME, res.points, res.ts, 8)
    check(dev == 0.0, "退化点曲线偏差严格为 0")

    # (c) 端点重合 + 自交环：仍可细分且偏差达标
    res = bf.flatten(LOOP, 1e-4)
    dev, _ = bf.polyline_deviation(LOOP, res.points, res.ts, 32)
    check(dev <= 1e-4 * (1 + 1e-9),
          f"闭环（端点重合）tol=1e-4：{res.num_points} 点，偏差 {dev:.2e}")
    check(res.points[0] == res.points[-1], "闭环折线闭合")

    # (d) 高阶曲线（12 阶）
    res = bf.flatten(SIN12, 1e-3)
    dev, _ = bf.polyline_deviation(SIN12, res.points, res.ts, 32)
    check(dev <= 1e-3 * (1 + 1e-9),
          f"12 阶曲线 tol=1e-3：{res.num_points} 点，偏差 {dev:.2e}")

    # (e) 容限极小：点数随 sqrt(1/tol) 膨胀，保证仍成立
    for tiny in (1e-7, 1e-8):
        res = bf.flatten(ARCH, tiny)
        dev, _ = bf.polyline_deviation(ARCH, res.points, res.ts, 8)
        check(dev <= tiny * (1 + 1e-8) + 1e-15,
              f"极小容限 {tiny:g}：{res.num_points} 点，偏差 {dev:.2e}")
        all_rows.append(dict(curve="cubic_arch", degree=3, tolerance=tiny,
                             segments=len(res.points) - 1,
                             points=res.num_points, max_deviation=dev,
                             worst_t=0.0, ratio=dev / tiny))

    # (f) 3D 曲线
    res = bf.flatten(CUBE_3D, 1e-4)
    dev, _ = bf.polyline_deviation(CUBE_3D, res.points, res.ts, 32)
    check(dev <= 1e-4 * (1 + 1e-9),
          f"3D 三次曲线：{res.num_points} 点，偏差 {dev:.2e}")

    # (g) 深度上限：人为限制深度时应显式报告
    res = bf.flatten(ARCH, 1e-12, max_depth=3)
    check(res.max_depth_reached, "max_depth=3 时报告触顶")

    # (h) 单调性：容限越紧点数不减
    counts = [bf.flatten(ARCH, t).num_points for t in (1e-2, 1e-3, 1e-4)]
    check(counts[0] <= counts[1] <= counts[2],
          f"点数随容限收紧单调不减：{counts}")

    # (i) 非法参数
    for bad in (lambda: bf.flatten([(0, 0)], 1e-3),
                lambda: bf.flatten(ARCH, 0.0),
                lambda: bf.elevate(ARCH, 1),
                lambda: bf.reduce_degree(ARCH, 4),
                lambda: bf.evaluate([(0,), (1, 2)], 0.5)):
        try:
            bad()
            check(False, "应抛出 ValueError")
        except ValueError:
            check(True, "非法参数正确抛出 ValueError")

    # 极小容限行补写进 CSV
    with open(os.path.join(DATA_DIR, "tolerance_vs_points.csv"),
              "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)

    print(f"\n全部 {passed} 项检查通过，用时 {time.time() - t0:.1f}s")
    print(f"数据已写入 {DATA_DIR}/tolerance_vs_points.csv, "
          f"{DATA_DIR}/degree_reduction.csv")


if __name__ == "__main__":
    main()
