#!/usr/bin/env python3
"""grid_planner 自测与数据报告（仅标准库，无第三方依赖）。

运行：python3 selftest.py
退出码 0 表示全部断言通过；同时在终端打印并写入 report.txt。

覆盖：
1. 启发函数可采纳性验证（开放网格精确相等 + 随机障碍网格对照 Dijkstra 真值）；
2. 穿角检测为零（A* 逐步路径与平滑后路径，含专门构造的"缝隙"地图）；
3. 路径平滑前后的转折点数与路径长度；
4. 边界情形：无解、起点=终点、窄通道、完全开放、起/终点为障碍。
"""

import math
import random
import sys

import grid_planner as gp

TOL = 1e-9
LINES = []


def out(text=""):
    print(text)
    LINES.append(text)


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


# ---------------------------------------------------------------- 场景地图

def scenario_open():
    grid = gp.Grid(20, 20)
    return "完全开放 20x20", grid, (0, 0), (19, 19)


def scenario_narrow():
    # 之字形窄通道：通道宽 1，墙开口左右交错
    lines = [
        "#############",
        "#...........#",
        "###########.#",
        "#...........#",
        "#.###########",
        "#...........#",
        "###########.#",
        "#...........#",
        "#.###########",
        "#...........#",
        "#############",
    ]
    grid = gp.Grid.from_lines(lines)
    return "窄通道（之字形，宽 1）", grid, (1, 1), (9, 11)


def scenario_rooms():
    # 房间 + 窄门：既有开阔区域也有瓶颈，平滑效果明显
    lines = [
        "#############",
        "#.....#.....#",
        "#.....#.....#",
        "#.....#.....#",
        "###.##.##.###",
        "#.....#.....#",
        "#...........#",
        "#.....#.....#",
        "###.##.##.###",
        "#.....#.....#",
        "#.....#.....#",
        "#.....#.....#",
        "#############",
    ]
    grid = gp.Grid.from_lines(lines)
    return "四房间 + 窄门", grid, (1, 1), (11, 11)


def scenario_unsolvable():
    # 终点被围墙完全封死
    lines = [
        "..........",
        ".########.",
        ".#......#.",
        ".#.####.#.",
        ".#.#..#.#.",
        ".#.#G.#.#.",
        ".#.####.#.",
        ".#......#.",
        ".########.",
        "..........",
    ]
    grid = gp.Grid.from_lines(lines, wall_chars="#")
    return "无解（终点被围死）", grid, (0, 0), (5, 4)


def scenario_same_point():
    grid = gp.Grid(8, 8, blocked={(3, 3), (3, 4), (4, 3)})
    return "起点=终点", grid, (5, 5), (5, 5)


def scenario_corner_trap():
    # 沿主对角线的障碍链：最直接的对角走法每一步都会穿角，必须绕行
    lines = [
        "..........",
        ".##.......",
        "..#.......",
        "...#......",
        "....##....",
        ".....#....",
        "......#...",
        ".......##.",
        "..........",
        "..........",
    ]
    grid = gp.Grid.from_lines(lines)
    return "穿角陷阱（对角障碍链）", grid, (0, 0), (9, 9)


# ---------------------------------------------------------------- 测试

def test_scenarios():
    out("=" * 72)
    out("1. 场景测试：路径数据（平滑前后转折点数 / 路径长度）与穿角断言")
    out("=" * 72)
    scenarios = [
        scenario_open(),
        scenario_narrow(),
        scenario_rooms(),
        scenario_same_point(),
        scenario_corner_trap(),
    ]
    header = ("%-22s %6s %8s | %8s %8s | %10s %10s"
              % ("场景", "顶点", "转折", "顶点*", "转折*", "长度", "长度*"))
    out(header)
    for name, grid, start, goal in scenarios:
        path, cost = gp.astar(grid, start, goal)
        check(path is not None, "%s 应有解" % name)
        check(path[0] == start and path[-1] == goal, "%s 起终点不符" % name)
        # 最优性：A* 代价与 Dijkstra 真值一致
        dist = gp.dijkstra_dist(grid, start)
        check(abs(cost - dist[goal]) < TOL, "%s A* 非最优" % name)
        # 穿角断言（逐步路径）
        step_cuts = gp.count_step_corner_cuts(grid, path)
        check(step_cuts == 0, "%s 原始路径存在穿角" % name)
        bh, cc = gp.count_path_issues(grid, path)
        check(bh == 0 and cc == 0, "%s 原始路径穿格/穿角" % name)
        # 平滑
        smoothed = gp.smooth_path(grid, path)
        sbh, scc = gp.count_path_issues(grid, smoothed)
        check(sbh == 0 and scc == 0, "%s 平滑路径穿格/穿角" % name)
        len_raw, len_sm = gp.path_length(path), gp.path_length(smoothed)
        check(len_sm <= len_raw + TOL, "%s 平滑后变长" % name)
        euclid = math.hypot(goal[0] - start[0], goal[1] - start[1])
        check(len_sm >= euclid - 1e-6, "%s 平滑后短于欧氏下界" % name)
        out("%-22s %6d %8d | %8d %8d | %10.4f %10.4f"
            % (name, len(path), gp.count_turns(path),
               len(smoothed), gp.count_turns(smoothed), len_raw, len_sm))
        out("    穿角检测：原始路径 %d 处，平滑路径 %d 处（断言为 0）" % (step_cuts, scc))
        if name.startswith("窄通道") or name.startswith("四房间"):
            out("    原始路径：")
            out(gp.render(grid, path))
            out("    平滑路径：")
            out(gp.render(grid, smoothed))
    out("注：顶点*/转折*/长度* 为平滑后的数值。平滑路径是任意角折线，")
    out("    可短于 8 邻接网格最优代价，但不低于起终点欧氏直线距离（已断言）。")
    out()


def test_unsolvable():
    out("=" * 72)
    out("2. 无解场景")
    out("=" * 72)
    name, grid, start, goal = scenario_unsolvable()
    path, cost = gp.astar(grid, start, goal)
    check(path is None and math.isinf(cost), "无解场景应返回 (None, inf)")
    out("%s：返回 path=None, cost=inf，符合预期。" % name)
    out(gp.render(grid))
    out()


def test_admissibility():
    out("=" * 72)
    out("3. 启发函数可采纳性验证：h(n) <= 真实最短距离 d(n)")
    out("=" * 72)
    out("理论：octile 距离 = 无障时的精确最短代价，障碍只会使真实代价变大，")
    out("故 h 永不高估（可采纳）；且满足一致性 h(a) <= c(a,b) + h(b)。")
    out()
    # 3a. 开放网格：h 与 Dijkstra 真值完全相等
    grid = gp.Grid(30, 30)
    goal = (29, 29)
    dist = gp.dijkstra_dist(grid, goal)
    n_cells = len(dist)
    max_gap = max(dist[c] - gp.octile(c, goal) for c in dist)
    n_equal = sum(1 for c in dist if abs(dist[c] - gp.octile(c, goal)) < TOL)
    out("[开放网格 30x30] 验证点数 %d" % n_cells)
    out("  h == d 的点数：%d（%.1f%%），max(h - d) = %.3e（<= 0 即不高估）"
        % (n_equal, 100.0 * n_equal / n_cells, max_gap))
    check(max_gap < TOL, "开放网格出现高估")
    # 3b. 随机障碍网格：h <= d 严格成立
    rng = random.Random(20261004)
    trials = 0
    checked = 0
    max_ratio = 0.0
    worst_gap = -math.inf
    for _ in range(40):
        g = gp.Grid(25, 25, blocked={(rng.randrange(25), rng.randrange(25))
                                     for _ in range(150)})
        goal = (24, 24)
        if not g.is_free(goal):
            continue
        dist = gp.dijkstra_dist(g, goal)
        for cell, d in dist.items():
            h = gp.octile(cell, goal)
            check(h <= d + TOL, "可采纳性被破坏: h=%f d=%f cell=%r" % (h, d, cell))
            checked += 1
            if d > 0:
                max_ratio = max(max_ratio, h / d)
            worst_gap = max(worst_gap, h - d)
        trials += 1
    out("[随机障碍网格] %d 张 25x25 地图（障碍 150 格/张），共验证 %d 个 (点, 终点) 组合"
        % (trials, checked))
    out("  全部满足 h <= d；max(h - d) = %.3e；max(h/d) = %.4f（<= 1 即不高估）"
        % (worst_gap, max_ratio))
    # 3c. 一致性抽查（A* 效率的正确性前提）
    g = gp.Grid(25, 25, blocked={(rng.randrange(25), rng.randrange(25))
                                 for _ in range(150)})
    goal = (24, 24)
    worst_cons = -math.inf
    n_edges = 0
    for r in range(25):
        for c in range(25):
            a = (r, c)
            if not g.is_free(a):
                continue
            for b, cost in g.neighbors(a):
                worst_cons = max(worst_cons, gp.octile(a, goal) - cost - gp.octile(b, goal))
                n_edges += 1
    out("[一致性] 抽查 %d 条边，max(h(a) - c(a,b) - h(b)) = %.3e（<= 0 即一致）"
        % (n_edges, worst_cons))
    check(worst_cons < TOL, "一致性被破坏")
    out()


def test_fuzz():
    out("=" * 72)
    out("4. 随机模糊测试：最优性 + 穿角为零 + 平滑合法（200 组）")
    out("=" * 72)
    rng = random.Random(199)
    solved = 0
    unsolvable = 0
    for i in range(200):
        rows = rng.randint(5, 30)
        cols = rng.randint(5, 30)
        density = rng.choice([0.1, 0.2, 0.3])
        blocked = {(r, c) for r in range(rows) for c in range(cols)
                   if rng.random() < density}
        grid = gp.Grid(rows, cols, blocked)
        free = [c for c in ((r, c) for r in range(rows) for c in range(cols))
                if grid.is_free(c)]
        if len(free) < 2:
            continue
        start, goal = rng.sample(free, 2)
        path, cost = gp.astar(grid, start, goal)
        dist = gp.dijkstra_dist(grid, start)
        if path is None:
            check(goal not in dist, "A* 报无解但 Dijkstra 可达")
            unsolvable += 1
            continue
        solved += 1
        check(abs(cost - dist[goal]) < TOL, "A* 非最优")
        check(abs(gp.path_length(path) - cost) < TOL, "路径长度与代价不符")
        check(gp.count_step_corner_cuts(grid, path) == 0, "原始路径穿角")
        bh, cc = gp.count_path_issues(grid, path)
        check(bh == 0 and cc == 0, "原始路径穿格/穿角")
        smoothed = gp.smooth_path(grid, path)
        sbh, scc = gp.count_path_issues(grid, smoothed)
        check(sbh == 0 and scc == 0, "平滑路径穿格/穿角")
        check(gp.path_length(smoothed) <= gp.path_length(path) + TOL, "平滑后变长")
        check(smoothed[0] == start and smoothed[-1] == goal, "平滑路径起终点改变")
    out("200 组随机地图：有解 %d，无解 %d；全部断言通过。" % (solved, unsolvable))
    out()


def test_edge_cases():
    out("=" * 72)
    out("5. 边界用例")
    out("=" * 72)
    # 起点=终点
    _, grid, s, g2 = scenario_same_point()
    path, cost = gp.astar(grid, s, g2)
    check(path == [s] and cost == 0.0, "起点=终点应返回单点零代价路径")
    out("起点=终点：path=%r, cost=%.1f ✓" % (path, cost))
    # 起点/终点为障碍
    for bad_start, bad_goal in (((0, 0), (1, 1)), ((1, 1), (0, 0))):
        gmap = gp.Grid(3, 3, blocked={(0, 0)})
        try:
            gp.astar(gmap, bad_start, bad_goal)
            raise SystemExit("障碍起终点应抛 ValueError")
        except ValueError as exc:
            out("障碍起终点：正确抛出 ValueError（%s）" % exc)
    # 越界
    try:
        gp.astar(gp.Grid(3, 3), (-1, 0), (2, 2))
        raise SystemExit("越界起点应抛 ValueError")
    except ValueError as exc:
        out("越界起点：正确抛出 ValueError（%s）" % exc)
    # 单格地图
    path, cost = gp.astar(gp.Grid(1, 1), (0, 0), (0, 0))
    check(path == [(0, 0)] and cost == 0.0, "单格地图")
    out("1x1 地图：path=[(0, 0)], cost=0.0 ✓")
    # 单行地图
    path, cost = gp.astar(gp.Grid(1, 5), (0, 0), (0, 4))
    check(cost == 4.0, "单行地图")
    out("1x5 单行地图：cost=4.0 ✓")
    out()


def main():
    out("grid_planner 自测报告  python=%s" % sys.version.split()[0])
    out()
    test_scenarios()
    test_unsolvable()
    test_admissibility()
    test_fuzz()
    test_edge_cases()
    out("=" * 72)
    out("全部断言通过 ✓")
    out("=" * 72)
    with open("report.txt", "w", encoding="utf-8") as fh:
        fh.write("\n".join(LINES) + "\n")
    print("\n报告已写入 report.txt")


if __name__ == "__main__":
    main()
