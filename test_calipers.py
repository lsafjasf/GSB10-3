"""hull_calipers 自测：暴力对拍 + 边界用例 + 朝向/面积断言。

运行：python3 test_calipers.py
"""

import math
import random

from hull_calipers import (
    convex_hull, hull_diameter, min_enclosing_rectangle,
    signed_area2, is_ccw, validate_ccw_polygon,
    cross, cross2, dot2, sub, dist2,
)


# ---------- 暴力法（对拍基准） ----------

def brute_diameter(pts):
    """O(n^2) 枚举所有点对。"""
    best_d2, best = -1, None
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d2 = dist2(pts[i], pts[j])
            if d2 > best_d2:
                best_d2, best = d2, (pts[i], pts[j])
    return math.sqrt(max(best_d2, 0)), best


def brute_min_rect(hull):
    """枚举凸包每条边作矩形底边方向，O(h^2)，不使用单调指针。"""
    h = len(hull)
    if h <= 2:
        return 0.0
    best = math.inf
    for i in range(h):
        a, b = hull[i], hull[(i + 1) % h]
        e = sub(b, a)
        len2 = dot2(e, e)
        hi = max(cross2(e, sub(p, a)) for p in hull)
        lo_p = min(dot2(e, p) for p in hull)
        hi_p = max(dot2(e, p) for p in hull)
        best = min(best, hi * (hi_p - lo_p) / len2)
    return best


# ---------- 通用校验 ----------

def check_hull_valid(hull, pts):
    """凸包合法性：CCW、凸、所有点都在内部或边界上。"""
    if len(hull) >= 3:
        validate_ccw_polygon(hull)          # 朝向 + 面积断言
        assert is_ccw(hull)
        assert signed_area2(hull) > 0
        for p in pts:
            for i in range(len(hull)):
                a, b = hull[i], hull[(i + 1) % len(hull)]
                assert cross(a, b, p) >= -1e-9, f"点 {p} 在凸包外"


def check_rect_covers(rect_corners, pts, area):
    """矩形四角自洽：面积吻合、所有点被覆盖。"""
    c = rect_corners
    w = math.hypot(c[1][0] - c[0][0], c[1][1] - c[0][1])
    hgt = math.hypot(c[3][0] - c[0][0], c[3][1] - c[0][1])
    assert abs(w * hgt - area) <= 1e-6 * max(1.0, area)
    if len(c) == 4 and w > 0 and hgt > 0:
        u = ((c[1][0] - c[0][0]) / w, (c[1][1] - c[0][1]) / w)
        n = (-u[1], u[0])
        du = [dot2(p, u) for p in pts]
        dn = [dot2(p, n) for p in pts]
        tol = 1e-7
        assert min(du) >= dot2(c[0], u) - tol and max(du) <= dot2(c[1], u) + tol
        assert min(dn) >= dot2(c[0], n) - tol and max(dn) <= dot2(c[2], n) + tol


def run_case(pts, tag):
    hull = convex_hull(pts)
    check_hull_valid(hull, pts)

    # 直径：旋转卡壳 vs 暴力
    d_cal, pair = hull_diameter(hull)
    d_brt, _ = brute_diameter(list(set(pts)))
    assert abs(d_cal - d_brt) <= 1e-9 * max(1.0, d_brt), \
        f"[{tag}] 直径不一致: calipers={d_cal} brute={d_brt}"
    # 返回的点对确实达到该距离
    assert abs(math.sqrt(dist2(*pair)) - d_cal) <= 1e-9 * max(1.0, d_cal)

    # 最小包围矩形：旋转卡壳 vs 暴力
    a_cal, corners = min_enclosing_rectangle(hull)
    a_brt = brute_min_rect(hull)
    assert abs(a_cal - a_brt) <= 1e-7 * max(1.0, a_brt), \
        f"[{tag}] 矩形面积不一致: calipers={a_cal} brute={a_brt}"
    check_rect_covers(corners, pts, a_cal)
    return hull, d_cal, a_cal


# ---------- 边界用例 ----------

def test_degenerate():
    # 点数 < 3
    run_case([(0, 0)], "单点")
    run_case([(0, 0), (3, 4)], "两点")
    run_case([], "空") if False else None  # 空集不在契约内，跳过

    # 全部重合
    run_case([(2, 2)] * 10, "全部重合")

    # 全部共线（水平 / 斜线 / 含重合）
    run_case([(i, 0) for i in range(8)], "共线-水平")
    run_case([(i, 2 * i) for i in range(-4, 5)], "共线-斜线")
    run_case([(0, 0), (1, 1), (1, 1), (2, 2), (2, 2), (5, 5)], "共线-含重合")

    # 共线 + 内部点混合
    run_case([(0, 0), (4, 0), (4, 4), (0, 4), (2, 0), (0, 2), (2, 2), (1, 1)],
             "正方形+边界共线点+内部点")
    print("边界用例通过")


def test_collinear_policy():
    pts = [(0, 0), (2, 0), (4, 0), (4, 3), (2, 3), (0, 3), (2, 2)]
    # 默认：剔除边界共线点，只留拐角
    h1 = convex_hull(pts)
    assert h1 == [(0, 0), (4, 0), (4, 3), (0, 3)], h1
    # 保留：边界共线点全部按外轮廓顺序保留，内部点 (2,2) 不保留
    h2 = convex_hull(pts, keep_collinear=True)
    assert h2 == [(0, 0), (2, 0), (4, 0), (4, 3), (2, 3), (0, 3)], h2
    assert (2, 2) not in h2
    # 全部共线：默认两端点；保留则全序列
    line = [(3, 3), (0, 0), (6, 6), (1, 1), (0, 0)]
    assert convex_hull(line) == [(0, 0), (6, 6)]
    assert convex_hull(line, keep_collinear=True) == [(0, 0), (1, 1), (3, 3), (6, 6)]
    # 两种策略的几何结果一致
    assert hull_diameter(h1)[0] == hull_diameter(h2)[0]
    assert min_enclosing_rectangle(h1)[0] == min_enclosing_rectangle(h2)[0]
    print("共线点保留策略测试通过")


def test_circle():
    R = 100.0
    for n in (6, 8, 36, 100):
        pts = [(R * math.cos(2 * math.pi * i / n),
                R * math.sin(2 * math.pi * i / n)) for i in range(n)]
        # 加重合点与圆心，不影响结果
        pts += [pts[0], (0.0, 0.0)]
        hull, d, area = run_case(pts, f"圆上{n}点")
        # 偶数正多边形：直径 = 2R（对径点）
        if n % 2 == 0:
            assert abs(d - 2 * R) < 1e-9, (n, d)
        # 最小包围矩形面积不超过外接正方形 (2R)^2，且不小于凸包面积
        assert area <= 4 * R * R + 1e-6
        assert area >= signed_area2(hull) / 2 - 1e-6
    print("圆上点用例通过")


def test_orientation_area():
    sq = [(0, 0), (4, 0), (4, 3), (0, 3)]
    assert is_ccw(sq) and signed_area2(sq) == 24.0
    assert not is_ccw(list(reversed(sq)))
    validate_ccw_polygon(sq)
    # 顺时针必须被拒绝
    try:
        validate_ccw_polygon(list(reversed(sq)))
        raise SystemExit("应当断言失败")
    except AssertionError:
        pass
    # 三角形面积
    assert signed_area2([(0, 0), (2, 0), (0, 2)]) == 4.0
    print("朝向与面积断言通过")


def test_fuzz(rounds=300, seed=174):
    rng = random.Random(seed)
    for t in range(rounds):
        mode = t % 4
        n = rng.randint(1, 30)
        if mode == 0:      # 整数网格（大量共线/重合）
            pts = [(rng.randint(-5, 5), rng.randint(-5, 5)) for _ in range(n)]
        elif mode == 1:    # 圆上加噪声
            pts = [(round(10 * math.cos(a) + rng.uniform(-1, 1), 3),
                    round(10 * math.sin(a) + rng.uniform(-1, 1), 3))
                   for a in [rng.uniform(0, 2 * math.pi) for _ in range(n)]]
        elif mode == 2:    # 几乎共线
            pts = [(i, rng.choice([0, 0, 0, 1])) for i in range(n)]
        else:              # 浮点随机
            pts = [(rng.uniform(-50, 50), rng.uniform(-50, 50)) for _ in range(n)]
        run_case(pts, f"fuzz#{t}")
        # keep_collinear 模式的 hull 同样过卡壳对拍
        hk = convex_hull(pts, keep_collinear=True)
        if len(hk) >= 3 and signed_area2(hk) > 1e-9:
            validate_ccw_polygon(hk)
            dk, _ = hull_diameter(hk)
            d2, _ = brute_diameter(list(set(pts)))
            assert abs(dk - d2) <= 1e-9 * max(1.0, d2)
            ak, _ = min_enclosing_rectangle(hk)
            ab = brute_min_rect(convex_hull(pts))
            assert abs(ak - ab) <= 1e-7 * max(1.0, ab), (ak, ab, t)
    print(f"随机对拍 {rounds} 轮通过（种子 {seed}，含 keep_collinear 对拍）")


if __name__ == "__main__":
    test_degenerate()
    test_collinear_policy()
    test_orientation_area()
    test_circle()
    test_fuzz()
    print("全部测试通过 ✔")
