"""bezier.py 自测：边界用例 + 容限/点数关系 + 偏差验证数据。

运行：python3 selftest.py
所有断言通过时退出码为 0，并打印数据表格。
"""

import math
import random

from bezier import (
    curve_to_curve_deviation,
    degree_reduction_error_bound,
    elevate,
    evaluate,
    flatten,
    flatten_segments,
    flatness,
    leaf_deviation,
    measured_flatten_deviation,
    polyline_deviation,
    reduce_degree,
)

random.seed(42)
PASS = 0


def check(name, condition, detail=""):
    global PASS
    status = "PASS" if condition else "FAIL"
    if condition:
        PASS += 1
    print(f"[{status}] {name} {detail}")
    if not condition:
        raise SystemExit(f"测试失败: {name}")


# ---------------------------------------------------------------------------
# 1. 边界用例
# ---------------------------------------------------------------------------
print("== 边界用例 ==")

# 1a. 控制点共线：曲线本身就是直线，任意容限下应只输出 2 个点
line = [(0.0, 0.0), (1.0, 0.5), (2.0, 1.0), (3.0, 1.5), (4.0, 2.0)]
poly = flatten(line, 1e-9)
dev, _ = measured_flatten_deviation(line, 1e-9)
check("退化直线(共线控制点)", len(poly) == 2 and dev < 1e-12,
      f"点数={len(poly)} 实测偏差={dev:.2e}")

# 1b. 所有控制点重合：曲线退化为一个点
blob = [(2.0, 3.0)] * 5
poly = flatten(blob, 1e-6)
check("控制点全部重合", len(poly) == 2 and poly[0] == poly[1] == (2.0, 3.0),
      f"点数={len(poly)}")

# 1c. 部分控制点重合 + 首尾重合（弦退化为点，走退化分支）
loop = [(0.0, 0.0), (0.0, 0.0), (3.0, 4.0), (0.0, 0.0)]
poly = flatten(loop, 1e-3)
dev, _ = measured_flatten_deviation(loop, 1e-3)
check("首尾/相邻控制点重合", dev <= 1e-3 * 1.05,
      f"点数={len(poly)} 实测偏差={dev:.2e} 容限=1e-3")

# 1d. 高阶曲线（10 阶，随机控制点）
high = [(random.uniform(-5, 5), random.uniform(-5, 5)) for _ in range(11)]
poly = flatten(high, 1e-3)
dev, _ = measured_flatten_deviation(high, 1e-3)
check("高阶曲线(10阶)", dev <= 1e-3 * 1.05,
      f"点数={len(poly)} 实测偏差={dev:.2e} 容限=1e-3")

# 1e. 极小容限：递归必须终止且偏差达标
cubic = [(0.0, 0.0), (1.0, 3.0), (3.0, -3.0), (4.0, 0.0)]
poly = flatten(cubic, 1e-9)
dev, _ = measured_flatten_deviation(cubic, 1e-9)
check("极小容限(1e-9)", dev <= 1e-9 * 1.05,
      f"点数={len(poly)} 实测偏差={dev:.2e}")

# 1f. 一阶曲线（本身就是线段）
seg = [(0.0, 0.0), (5.0, 5.0)]
check("一阶曲线", flatten(seg, 1e-12) == [seg[0], seg[1]])

# 1g. 三维曲线（任意维度）
c3d = [(0, 0, 0), (1, 2, 3), (3, -1, 1), (4, 0, 2)]
dev, _ = measured_flatten_deviation(c3d, 1e-4)
check("三维曲线", dev <= 1e-4 * 1.05, f"实测偏差={dev:.2e}")

# 1h. 尖点（控制点折返，曲线有急转弯）
cusp = [(0.0, 0.0), (2.0, 0.0), (0.5, 0.0), (2.0, 1.0)]
dev, _ = measured_flatten_deviation(cusp, 1e-4)
check("尖点/折返曲线", dev <= 1e-4 * 1.05, f"实测偏差={dev:.2e}")

# ---------------------------------------------------------------------------
# 2. 容限 -> 点数 / 实测偏差 关系数据
# ---------------------------------------------------------------------------
print("\n== 容限 vs 点数 / 实测偏差 ==")
curves = {
    "三次曲线": [(0.0, 0.0), (1.0, 3.0), (3.0, -3.0), (4.0, 0.0)],
    "七次曲线": [(0.0, 0.0), (1.0, 4.0), (2.0, -3.0), (3.0, 3.0),
                 (4.0, -2.0), (5.0, 2.0), (6.0, -1.0), (7.0, 0.0)],
}
header = f"{'曲线':<10}{'容限':>10}{'点数':>8}{'实测偏差':>14}{'偏差/容限':>10}"
print(header)
print("-" * len(header))
for name, ctrl in curves.items():
    for exp in range(1, 8):
        tol = 10.0 ** (-exp)
        polyline = flatten(ctrl, tol)
        dev, _ = measured_flatten_deviation(ctrl, tol)
        ratio = dev / tol
        print(f"{name:<10}{tol:>10.0e}{len(polyline):>8}{dev:>14.3e}{ratio:>10.3f}")
        check(f"{name} tol=1e-{exp} 偏差达标", ratio <= 1.05,
              f"ratio={ratio:.3f}")

# ---------------------------------------------------------------------------
# 3. 折线整体偏差验证（独立路径：整条折线 vs 曲线）
# ---------------------------------------------------------------------------
print("\n== 折线整体偏差验证（独立采样路径）==")
for exp in (2, 4, 6):
    tol = 10.0 ** (-exp)
    polyline = flatten(cubic, tol)
    dev = polyline_deviation(cubic, polyline)
    print(f"tol={tol:.0e} 点数={len(polyline)} 整体实测偏差={dev:.3e} "
          f"(<= 容限: {dev <= tol * 1.05})")
    check(f"整体偏差 tol=1e-{exp}", dev <= tol * 1.05)

# ---------------------------------------------------------------------------
# 4. 降阶：误差界 vs 实测误差
# ---------------------------------------------------------------------------
print("\n== 降阶误差（上界 vs 实测）==")
cases = [
    ("三次->二次", [(0.0, 0.0), (1.0, 2.0), (3.0, 2.0), (4.0, 0.0)], 2),
    ("四次->三次", [(0.0, 0.0), (1.0, 3.0), (2.0, -2.0), (3.0, 3.0), (4.0, 0.0)], 3),
    ("六次->三次", [(0.0, 0.0), (1.0, 2.0), (2.0, -2.0), (3.0, 2.0),
                    (4.0, -2.0), (5.0, 2.0), (6.0, 0.0)], 3),
    ("六次->一次", [(0.0, 0.0), (1.0, 2.0), (2.0, -2.0), (3.0, 2.0),
                    (4.0, -2.0), (5.0, 2.0), (6.0, 0.0)], 1),
]
print(f"{'用例':<14}{'误差上界':>14}{'实测误差':>14}{'上界/实测':>10}")
for name, ctrl, target in cases:
    reduced = reduce_degree(ctrl, target)
    bound = degree_reduction_error_bound(ctrl, reduced)
    measured = curve_to_curve_deviation(ctrl, reduced)
    print(f"{name:<14}{bound:>14.6e}{measured:>14.6e}{bound / max(measured, 1e-300):>10.2f}")
    check(f"降阶误差界有效({name})", measured <= bound * (1 + 1e-9),
          f"bound={bound:.3e} measured={measured:.3e}")

# 升阶应保持曲线不变（验证 elevate 正确性，降阶误差界依赖它）
quartic = [(0.0, 0.0), (1.0, 2.0), (2.0, -1.0), (3.0, 2.0), (4.0, 0.0)]
raised = elevate(quartic, 3)
check("升阶不改变曲线", curve_to_curve_deviation(quartic, raised) < 1e-12)

# 降阶后再细分：组合流程端到端验证
reduced = reduce_degree(quartic, 2)
polyline = flatten(reduced, 1e-3)
dev = polyline_deviation(reduced, polyline)
total = dev + degree_reduction_error_bound(quartic, reduced)
print(f"\n组合流程: 四次->二次降阶 + 1e-3 细分")
print(f"  降阶误差上界={degree_reduction_error_bound(quartic, reduced):.3e} "
      f"细分偏差={dev:.3e} 总误差上界={total:.3e}")
check("降阶+细分组合", dev <= 1e-3 * 1.05)

print(f"\n全部 {PASS} 项检查通过。")
