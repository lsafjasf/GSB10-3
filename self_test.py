"""最近点对自测：边界用例 + 与暴力实现的随机对拍。

运行：python3 self_test.py
"""

import random
from math import inf, sqrt

from closest_pair import ClosestPair, all_pairs, brute_force, closest_pair

FAILED = 0


def check(name, cond, detail=""):
    global FAILED
    status = "PASS" if cond else "FAIL"
    if not cond:
        FAILED += 1
    print(f"[{status}] {name}" + (f"  {detail}" if detail else ""))


def assert_equal(name, points):
    """分治结果必须与暴力完全一致（距离、全部点对、重合分组）。"""
    r1 = closest_pair(points)
    r2 = brute_force(points)
    check(name, r1 == r2,
          f"dist={r1.distance!r} pairs={len(r1.pairs)} groups={len(r1.groups)}")


# ---------- 边界用例 ----------

def test_small():
    check("0 个点", closest_pair([]) == ClosestPair(inf, (), ()))
    check("1 个点", closest_pair([(3, 4)]) == ClosestPair(inf, (), ()))
    r = closest_pair([(0, 0), (3, 4)])
    check("2 个点", r.distance == 5.0 and r.pairs == ((0, 1),),
          f"dist={r.distance}")
    r = closest_pair([(0, 0), (1, 0), (0, 1)])
    check("3 个点两组并列", r.distance == 1.0 and set(r.pairs) == {(0, 1), (0, 2)},
          f"pairs={r.pairs}")


def test_duplicates():
    r = closest_pair([(5, 5)] * 5)
    check("全部重合 n=5", r.distance == 0.0 and r.groups == ((0, 1, 2, 3, 4),)
          and len(list(all_pairs(r))) == 10, f"groups={r.groups}")
    pts = [(0, 0), (0, 0), (1, 0), (5, 5), (5, 5), (5, 5)]
    r = closest_pair(pts)
    expect = {(0, 1), (3, 4), (3, 5), (4, 5)}
    check("多组重合点", r.distance == 0.0 and set(all_pairs(r)) == expect,
          f"groups={r.groups}")
    pts = [(0, 0), (2, 0), (1, 0), (1, 0)]
    r = closest_pair(pts)
    check("重合点夹杂", r.distance == 0.0 and set(all_pairs(r)) == {(2, 3)},
          f"groups={r.groups}")
    pts = [(1, 2), (1.0, 2.0), (9, 9)]
    r = closest_pair(pts)
    check("int/float 同值视为重合", r.distance == 0.0
          and set(all_pairs(r)) == {(0, 1)}, f"groups={r.groups}")


def test_collinear():
    pts = [(x, 0) for x in range(10)]
    r = closest_pair(pts)
    check("共线等距 10 点", r.distance == 1.0 and len(r.pairs) == 9,
          f"pairs={len(r.pairs)}")
    pts = [(x, 2 * x) for x in range(100)]
    r = closest_pair(pts)
    check("斜线共线 100 点", r.distance == sqrt(5) and len(r.pairs) == 99,
          f"dist={r.distance:.6f} pairs={len(r.pairs)}")
    pts = [(3, y) for y in range(-50, 50)]
    r = closest_pair(pts)
    check("竖直共线 100 点", r.distance == 1.0 and len(r.pairs) == 99,
          f"pairs={len(r.pairs)}")


def test_extreme():
    big = 10 ** 18
    pts = [(0, 0), (big, big), (big, -big), (-big, 0)]
    assert_equal("极端整数坐标 1e18", pts)
    pts = [(i, 0) for i in range(1000)] + [(10 ** 15, 10 ** 15)]
    r = closest_pair(pts)
    check("极端离群点", r.distance == 1.0 and len(r.pairs) == 999,
          f"pairs={len(r.pairs)}")
    pts = [(1e15, 1e15), (1e15 + 2.0, 1e15), (-1e15, -1e15)]
    r = closest_pair(pts)
    check("极端浮点坐标 1e15", r.distance == 2.0 and set(r.pairs) == {(0, 1)},
          f"dist={r.distance!r}")
    # 1e15 的 ULP 约为 0.125，sub-ULP 间距会被 IEEE 754 舍入为同一坐标（即重合）
    pts = [(1e15, 1e15), (1e15 + 1e-100, 1e15)]
    r = closest_pair(pts)
    check("sub-ULP 间距塌缩为重合", r.distance == 0.0
          and set(all_pairs(r)) == {(0, 1)}, f"dist={r.distance!r}")
    pts = [(-1e-9, -1e-9), (1e-9, 1e-9), (0, 0)]
    assert_equal("微小负坐标", pts)


def test_ties():
    pts = [(0, 0), (1, 0), (0, 1), (1, 1)]
    r = closest_pair(pts)
    check("正方形四边并列", r.distance == 1.0 and len(r.pairs) == 4,
          f"pairs={len(r.pairs)}")
    pts = [(0, 0), (3, 0), (6, 0), (0, 3), (3, 3), (6, 3)]
    r = closest_pair(pts)
    check("网格多组并列", r.distance == 3.0 and len(r.pairs) == 7,
          f"pairs={len(r.pairs)}")
    # 最近点对跨越分治中线
    pts = [(0, 0), (10, 0), (4, 0), (6, 0)]
    r = closest_pair(pts)
    check("跨中线最近对", r.distance == 2.0 and set(r.pairs) == {(2, 3)},
          f"pairs={r.pairs}")


# ---------- 随机对拍 ----------

def cross_check():
    rng = random.Random(20261004)
    total = 0
    cases = [
        ("均匀整数 0..100", lambda n: [(rng.randint(0, 100), rng.randint(0, 100))
                                       for _ in range(n)],
         [2, 3, 5, 10, 50, 200, 500], 30),
        ("均匀浮点 0..1", lambda n: [(rng.random(), rng.random())
                                     for _ in range(n)],
         [2, 3, 5, 10, 50, 200, 500], 30),
        ("网格点(大量并列)", lambda n: [(rng.randint(0, 10), rng.randint(0, 10))
                                        for _ in range(n)],
         [2, 3, 5, 10, 50, 120], 30),
        ("共线+扰动", lambda n: [(i, i * 2 + rng.randint(0, 3))
                                 for i in range(n)],
         [2, 3, 5, 10, 50, 200], 30),
    ]
    print("\n随机对拍（分治 vs 暴力，距离/点对/分组必须完全一致）：")
    print(f"{'数据类型':<16}{'n':>5}{'重复':>6}{'一致':>8}")
    for name, gen, sizes, trials in cases:
        for n in sizes:
            ok = 0
            for _ in range(trials):
                pts = gen(n)
                if closest_pair(pts) == brute_force(pts):
                    ok += 1
                else:
                    global FAILED
                    FAILED += 1
                    r1, r2 = closest_pair(pts), brute_force(pts)
                    print(f"  MISMATCH {name} n={n}: {r1} vs {r2}")
            total += trials
            print(f"{name:<16}{n:>5}{trials:>6}{ok:>6}/{trials}")
    check("随机对拍全部一致", FAILED == 0, f"共 {total} 组")


if __name__ == "__main__":
    test_small()
    test_duplicates()
    test_collinear()
    test_extreme()
    test_ties()
    cross_check()
    print(f"\n{'全部通过' if FAILED == 0 else f'{FAILED} 项失败'}")
    raise SystemExit(1 if FAILED else 0)
