"""
demo.py — 交付用演示：边界场景 + 路径数据 + 平滑对比 + 穿角断言

运行：python3 -m planner.demo
"""

import math
import random

from planner.grid_planner import (
    Grid, astar, smooth_path, validate_path, octile_heuristic,
)
from planner.test_grid_planner import dijkstra_true_cost, random_grid


def show(title, grid, start, goal):
    print(f"== {title} ==")
    path, info = astar(grid, start, goal)
    if not info["found"]:
        print(f"  无解 (扩展节点 {info['expanded']})\n")
        return
    check = validate_path(grid, path)
    sm, sinfo = smooth_path(grid, path)
    print(f"  原始: 点数 {len(path):3d}, 转折点 {info['turns']:2d}, "
          f"长度 {info['length']:8.3f}")
    print(f"  平滑: 点数 {len(sm):3d}, 转折点 {sinfo['turns']:2d}, "
          f"长度 {sinfo['length']:8.3f}")
    print(f"  穿角移动数(独立复核): {check['corner_cuts']}  "
          f"合法: {check['valid']}")
    print(f"  平滑后路径: {sm}")
    print()


def admissibility_report():
    rng = random.Random(20261004)
    pairs = violations = 0
    worst = 0.0
    ratios = []
    for _ in range(30):
        grid = random_grid(20, 20, 0.25, rng)
        free = [(x, y) for y in range(20) for x in range(20)
                if not grid.blocked(x, y)]
        rng.shuffle(free)
        for i in range(0, min(len(free) - 1, 40), 2):
            s, g = free[i], free[i + 1]
            true_cost = dijkstra_true_cost(grid, s, g)
            if math.isinf(true_cost) or true_cost < 1e-12:
                continue
            h = octile_heuristic(s, g)
            pairs += 1
            ratio = h / true_cost
            ratios.append(ratio)
            worst = max(worst, ratio)
            if h > true_cost + 1e-9:
                violations += 1
    print("== 启发函数可采纳性 ==")
    print(f"  采样点对: {pairs}, h > g* 违例: {violations} (必须 0)")
    print(f"  h/g* 最大 {worst:.4f}, 平均 {sum(ratios)/len(ratios):.4f}")
    print()


def main():
    admissibility_report()

    show("无解（整列墙隔离）",
         Grid.from_ascii("...#...\n" * 4), (0, 0), (6, 3))

    g = Grid([[0] * 6 for _ in range(6)])
    show("起点终点重合", g, (2, 2), (2, 2))

    show("窄通道（1 格宽缝，必经 (3,2)）",
         Grid.from_ascii("...#...\n"
                         "...#...\n"
                         ".......\n"
                         "...#...\n"
                         "...#...\n"),
         (0, 2), (6, 2))

    show("完全开放 8x8：(0,0)->(7,5)",
         Grid([[0] * 8 for _ in range(8)]), (0, 0), (7, 5))

    show("对角缝禁穿（两障碍夹角必须绕行）",
         Grid.from_ascii("....\n"
                         "..#.\n"
                         ".#..\n"
                         "....\n"),
         (1, 1), (3, 3))


if __name__ == "__main__":
    main()
