"""demo.py — 端到端示例：凸包 -> 最远点对 -> 最小包围矩形，并与暴力法对拍。"""

import math
import random

from geometry import (convex_hull, signed_area, is_ccw,
                      diameter_pairs, diameter_bruteforce,
                      min_area_rect, min_area_rect_bruteforce)

random.seed(2026)
pts = [(random.uniform(-50, 50), random.uniform(-50, 50)) for _ in range(30)]
pts += [(10.0, 10.0)] * 3          # 重合点
pts += [(float(i), 0.0) for i in range(5)]  # 共线点

hull = convex_hull(pts)
print(f"点数(含重复/共线): {len(pts)}")
print(f"凸包顶点数: {len(hull)}  朝向CCW: {is_ccw(hull)}  面积: {signed_area(hull):.4f}")

d, pairs = diameter_pairs(pts)
d_bf, _ = diameter_bruteforce(pts)
print(f"最远点对: {pairs[0]}  距离: {d:.6f}  暴力对拍: {d_bf:.6f}  一致: {math.isclose(d, d_bf)}")

r = min_area_rect(pts)
r_bf = min_area_rect_bruteforce(pts)
print(f"最小包围矩形: 面积 {r['area']:.6f}  宽 {r['width']:.4f}  高 {r['height']:.4f}"
      f"  角度 {math.degrees(r['angle']):.2f}°")
print(f"暴力对拍面积: {r_bf:.6f}  一致: {math.isclose(r['area'], r_bf, abs_tol=1e-7)}")
