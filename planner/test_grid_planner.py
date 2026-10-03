"""
test_grid_planner.py — 自测与验证数据输出

运行：python3 -m unittest planner.test_grid_planner -v
或直接：python3 planner/test_grid_planner.py
"""

import heapq
import math
import random
import unittest

from planner.grid_planner import (
    Grid, astar, octile_heuristic, smooth_path, line_of_sight,
    validate_path, _NEIGHBORS, _diagonal_cuts_corner,
)

EPS = 1e-9


def dijkstra_true_cost(grid, start, goal):
    """独立实现的 Dijkstra（与 A* 同一移动模型），给出真实最短代价。"""
    dist = {start: 0.0}
    pq = [(0.0, start)]
    while pq:
        d, cur = heapq.heappop(pq)
        if d > dist.get(cur, math.inf) + EPS:
            continue
        if cur == goal:
            return d
        cx, cy = cur
        for dx, dy, cost in _NEIGHBORS:
            nx, ny = cx + dx, cy + dy
            if dx != 0 and dy != 0:
                if _diagonal_cuts_corner(grid, cx, cy, dx, dy):
                    continue
            elif grid.blocked(nx, ny):
                continue
            nd = d + cost
            if nd < dist.get((nx, ny), math.inf) - EPS:
                dist[(nx, ny)] = nd
                heapq.heappush(pq, (nd, (nx, ny)))
    return math.inf


def random_grid(width, height, density, rng):
    cells = [[1 if rng.random() < density else 0
              for _ in range(width)] for _ in range(height)]
    return Grid(cells)


class TestAdmissibility(unittest.TestCase):
    """启发函数可采纳性：h(s,g) <= 真实最短代价 g*(s,g)。"""

    def test_octile_never_overestimates(self):
        rng = random.Random(20261004)
        pairs = 0
        worst_ratio = 0.0
        ratios = []
        violations = 0
        for _ in range(30):
            grid = random_grid(20, 20, 0.25, rng)
            free = [(x, y) for y in range(20) for x in range(20)
                    if not grid.blocked(x, y)]
            rng.shuffle(free)
            for i in range(0, min(len(free) - 1, 40), 2):
                s, g = free[i], free[i + 1]
                true_cost = dijkstra_true_cost(grid, s, g)
                if math.isinf(true_cost) or true_cost < EPS:
                    continue
                h = octile_heuristic(s, g)
                pairs += 1
                self.assertLessEqual(h, true_cost + EPS,
                                     f"h 高估: h={h} > g*={true_cost}")
                if h > true_cost + EPS:
                    violations += 1
                ratio = h / true_cost
                ratios.append(ratio)
                worst_ratio = max(worst_ratio, ratio)
        mean_ratio = sum(ratios) / len(ratios)
        print("\n[可采纳性验证] 随机地图 30 张(20x20, 障碍率0.25)")
        print(f"  采样点对数        : {pairs}")
        print(f"  h > g* 违例数     : {violations} (必须为 0)")
        print(f"  h/g* 最大值       : {worst_ratio:.4f} (<=1 即可采纳)")
        print(f"  h/g* 平均值       : {mean_ratio:.4f}")
        self.assertEqual(violations, 0)
        self.assertGreater(pairs, 100)

    def test_heuristic_tight_on_open_map(self):
        """完全开放地图上 h 应等于真实代价（紧的下界）。"""
        grid = Grid([[0] * 10 for _ in range(10)])
        s, g = (0, 0), (7, 4)
        true_cost = dijkstra_true_cost(grid, s, g)
        h = octile_heuristic(s, g)
        self.assertAlmostEqual(h, true_cost, places=9)
        print(f"\n[紧性] 开放地图 h={h:.4f} == g*={true_cost:.4f}")


class TestCornerCutting(unittest.TestCase):
    """对角线移动不得穿过障碍角。"""

    def test_diagonal_between_two_obstacles_forbidden(self):
        # (0,0) 与 (1,1) 之间的斜线被 (1,0)、(0,1) 两个障碍夹住 -> 无解
        grid = Grid.from_ascii(".#\n#.")
        path, info = astar(grid, (0, 0), (1, 1))
        self.assertEqual(path, [])
        self.assertFalse(info["found"])
        print("\n[穿角] 2x2 双障碍夹角 -> 正确判无解")

    def test_detour_around_corner(self):
        # 斜线被夹时必须绕行，且全程穿角次数为 0
        grid = Grid.from_ascii(
            "....\n"
            "..#.\n"
            ".#..\n"
            "....\n"
        )
        start, goal = (1, 1), (3, 3)
        path, info = astar(grid, start, goal)
        self.assertTrue(info["found"])
        check = validate_path(grid, path)
        self.assertTrue(check["valid"], check["reasons"])
        self.assertEqual(check["corner_cuts"], 0, "路径存在穿角移动")
        # 被禁止的斜步 (1,1)->(2,2) 不得出现
        steps = set(zip(path, path[1:]))
        self.assertNotIn(((1, 1), (2, 2)), steps)
        print(f"\n[穿角] 绕行路径 {path}")
        print(f"  穿角移动数(独立复核): {check['corner_cuts']} (必须为 0)")

    def test_zero_corner_cuts_on_random_maps(self):
        rng = random.Random(7)
        total_paths = 0
        total_cuts = 0
        for _ in range(20):
            grid = random_grid(15, 15, 0.3, rng)
            free = [(x, y) for y in range(15) for x in range(15)
                    if not grid.blocked(x, y)]
            if len(free) < 2:
                continue
            s, g = free[0], free[-1]
            path, info = astar(grid, s, g)
            if not info["found"]:
                continue
            total_paths += 1
            check = validate_path(grid, path)
            total_cuts += check["corner_cuts"]
            self.assertEqual(check["corner_cuts"], 0, check["reasons"])
            sm, _ = smooth_path(grid, path)
            for a, b in zip(sm, sm[1:]):
                self.assertTrue(line_of_sight(grid, a, b),
                                f"平滑后线段 {a}->{b} 非法")
        print(f"\n[穿角] 随机地图有效路径 {total_paths} 条, "
              f"穿角移动总数 {total_cuts} (必须为 0)")
        self.assertEqual(total_cuts, 0)


class TestLineOfSight(unittest.TestCase):
    """精确栅格遍历的视线判定回归。"""

    def test_ray_enters_intermediate_cell(self):
        # 线段 (0,0)->(2,1) 必经格 (1,1)，该格阻挡则视线不成立
        grid = Grid.from_ascii("...\n..#\n...")
        self.assertFalse(line_of_sight(grid, (0, 0), (2, 1)))
        open_grid = Grid.from_ascii("...\n...\n...")
        self.assertTrue(line_of_sight(open_grid, (0, 0), (2, 1)))

    def test_ray_through_grid_vertex(self):
        # 恰好过格点：两共享边格都挡 -> 穿角拒绝；只挡一侧 -> 允许
        pinched = Grid.from_ascii(".#\n#.")
        self.assertFalse(line_of_sight(pinched, (0, 0), (1, 1)))
        half = Grid.from_ascii(".#\n..")
        self.assertTrue(line_of_sight(half, (0, 0), (1, 1)))


class TestSmoothing(unittest.TestCase):
    """路径平滑：转折点与长度均应不增。"""

    def test_smoothing_reduces_turns_and_length(self):
        rng = random.Random(99)
        rows = []
        for _ in range(15):
            grid = random_grid(25, 25, 0.2, rng)
            free = [(x, y) for y in range(25) for x in range(25)
                    if not grid.blocked(x, y)]
            if len(free) < 2:
                continue
            s, g = free[0], free[-1]
            path, info = astar(grid, s, g)
            if not info["found"] or len(path) < 5:
                continue
            sm, sinfo = smooth_path(grid, path)
            self.assertLessEqual(sinfo["length"], info["length"] + EPS)
            self.assertLessEqual(sinfo["turns"], info["turns"])
            self.assertEqual(sm[0], s)
            self.assertEqual(sm[-1], g)
            rows.append((info["turns"], sinfo["turns"],
                         info["length"], sinfo["length"],
                         sinfo["removed"]))
        self.assertGreaterEqual(len(rows), 5)
        print("\n[平滑] 样本(转折点 前->后 | 长度 前->后 | 删除点数):")
        for t0, t1, l0, l1, rm in rows:
            print(f"  转折点 {t0:2d} -> {t1:2d} | "
                  f"长度 {l0:7.3f} -> {l1:7.3f} | 删除 {rm} 点")
        avg_t0 = sum(r[0] for r in rows) / len(rows)
        avg_t1 = sum(r[1] for r in rows) / len(rows)
        avg_l0 = sum(r[2] for r in rows) / len(rows)
        avg_l1 = sum(r[3] for r in rows) / len(rows)
        print(f"  平均: 转折点 {avg_t0:.1f} -> {avg_t1:.1f}, "
              f"长度 {avg_l0:.3f} -> {avg_l1:.3f}")
        self.assertLess(avg_t1, avg_t0)
        self.assertLess(avg_l1, avg_l0 + EPS)


class TestEdgeCases(unittest.TestCase):
    def test_no_solution(self):
        grid = Grid.from_ascii(
            "...#...\n"
            "...#...\n"
            "...#...\n"
            "...#...\n"
        )
        path, info = astar(grid, (0, 0), (6, 3))
        self.assertEqual(path, [])
        self.assertFalse(info["found"])
        print(f"\n[无解] 被墙隔离 -> found={info['found']}, "
              f"扩展节点 {info['expanded']}")

    def test_start_equals_goal(self):
        grid = Grid([[0] * 5 for _ in range(5)])
        path, info = astar(grid, (2, 2), (2, 2))
        self.assertEqual(path, [(2, 2)])
        self.assertTrue(info["found"])
        self.assertEqual(info["length"], 0.0)
        print(f"\n[起终重合] path={path}, length={info['length']}")

    def test_narrow_corridor(self):
        # 1 格宽竖缝：路径必须经过 (3,2)
        grid = Grid.from_ascii(
            "...#...\n"
            "...#...\n"
            ".......\n"
            "...#...\n"
            "...#...\n"
        )
        path, info = astar(grid, (0, 2), (6, 2))
        self.assertTrue(info["found"])
        self.assertIn((3, 2), path, "未穿过窄通道")
        check = validate_path(grid, path)
        self.assertTrue(check["valid"], check["reasons"])
        print(f"\n[窄通道] 路径经过 (3,2): {(3, 2) in path}, "
              f"长度 {info['length']:.3f}")

    def test_narrow_diagonal_gap_blocked_by_corner_rule(self):
        # 对角“窄缝”被两障碍夹住，禁止斜穿，必须走 1 格正交通道
        grid = Grid.from_ascii(
            ".....\n"
            ".##..\n"
            "..#..\n"
            "..##.\n"
            ".....\n"
        )
        path, info = astar(grid, (0, 0), (4, 4))
        self.assertTrue(info["found"])
        check = validate_path(grid, path)
        self.assertEqual(check["corner_cuts"], 0)
        print(f"\n[斜缝禁穿] 穿角数 {check['corner_cuts']}, "
              f"长度 {info['length']:.3f}")

    def test_fully_open_map(self):
        grid = Grid([[0] * 8 for _ in range(8)])
        path, info = astar(grid, (0, 0), (7, 5))
        self.assertTrue(info["found"])
        expected = math.sqrt(2) * 5 + 2  # 5 斜 + 2 直
        self.assertAlmostEqual(info["length"], expected, places=9)
        sm, sinfo = smooth_path(grid, path)
        self.assertEqual(len(sm), 2, "开放地图平滑后应为一条直线")
        self.assertAlmostEqual(sinfo["length"],
                               math.hypot(7, 5), places=9)
        print(f"\n[开放地图] 原始长度 {info['length']:.3f} (理论 {expected:.3f}), "
              f"平滑后 {sinfo['length']:.3f} (直线 {math.hypot(7, 5):.3f}), "
              f"转折点 {info['turns']} -> {sinfo['turns']}")

    def test_blocked_start_or_goal_raises(self):
        grid = Grid.from_ascii(".#\n..")
        with self.assertRaises(ValueError):
            astar(grid, (1, 0), (0, 0))
        with self.assertRaises(ValueError):
            astar(grid, (0, 0), (9, 9))


if __name__ == "__main__":
    unittest.main(verbosity=2)
