"""
grid_planner.py — 栅格地图 A* 路径规划（仅标准库）

特性：
  * 八邻域移动（4 直走代价 1，4 斜走代价 sqrt(2)）
  * 斜向移动禁止穿过障碍角（corner-cutting 检查）
  * 可采纳启发函数：octile distance（直走 1 / 斜走 sqrt(2) 的最短路下界）
  * 路径平滑：贪心视线（line-of-sight）拐角点削减，视线判定同样遵守穿角规则
  * 完整统计：路径长度、转折点数量、扩展节点数、穿角次数（恒为 0）

坐标系：grid[y][x]，0 = 自由，1 = 障碍。
"""

import heapq
import math

STRAIGHT_COST = 1.0
DIAG_COST = math.sqrt(2.0)

# 8 邻域：(dx, dy, cost)
_NEIGHBORS = (
    (1, 0, STRAIGHT_COST),
    (-1, 0, STRAIGHT_COST),
    (0, 1, STRAIGHT_COST),
    (0, -1, STRAIGHT_COST),
    (1, 1, DIAG_COST),
    (1, -1, DIAG_COST),
    (-1, 1, DIAG_COST),
    (-1, -1, DIAG_COST),
)


class Grid:
    def __init__(self, cells):
        """cells: 0/1 二维序列，grid[y][x]。"""
        self.height = len(cells)
        self.width = len(cells[0]) if self.height else 0
        self.cells = [list(row) for row in cells]
        for row in self.cells:
            if len(row) != self.width:
                raise ValueError("所有行宽度必须一致")

    def in_bounds(self, x, y):
        return 0 <= x < self.width and 0 <= y < self.height

    def blocked(self, x, y):
        return not self.in_bounds(x, y) or self.cells[y][x] == 1

    @staticmethod
    def from_ascii(text):
        """'#' 为障碍，其余字符为自由。忽略空行。"""
        rows = []
        for line in text.strip("\n").splitlines():
            line = line.strip()
            if line:
                rows.append([1 if ch == "#" else 0 for ch in line])
        return Grid(rows)


def octile_heuristic(a, b):
    """
    Octile distance —— 无障碍前提下八邻域最短路长度：
        h = sqrt(2)*min(dx,dy) + |dx-dy|
    任何障碍只会使最短路更长，因此 h <= 真实代价（可采纳）。
    """
    dx = abs(a[0] - b[0])
    dy = abs(a[1] - b[1])
    return DIAG_COST * min(dx, dy) + abs(dx - dy)


def _diagonal_cuts_corner(grid, x, y, dx, dy):
    """
    从 (x,y) 斜向移动到 (x+dx,y+dy) 时，是否会穿过障碍角：
    目标格本身阻挡，或共享该角的两个正交格都阻挡。
    两正交格均阻挡 => 穿过内角，禁止；
    只阻挡一个 => 沿障碍边缘斜贴（不穿角），允许。
    """
    nx, ny = x + dx, y + dy
    if grid.blocked(nx, ny):
        return True
    return grid.blocked(x + dx, y) and grid.blocked(x, y + dy)


def astar(grid, start, goal, heuristic=octile_heuristic):
    """
    返回 (path, info)。
      path: [(x, y), ...]，无解时为 []；start==goal 时为 [start]。
      info: {"length", "turns", "expanded", "corner_cuts", "found"}
    """
    sx, sy = start
    gx, gy = goal
    if not grid.in_bounds(sx, sy) or not grid.in_bounds(gx, gy):
        raise ValueError("起点/终点越界")
    if grid.blocked(sx, sy) or grid.blocked(gx, gy):
        raise ValueError("起点/终点位于障碍上")

    info = {"length": 0.0, "turns": 0, "expanded": 0,
            "corner_cuts": 0, "found": False}

    if start == goal:
        info["found"] = True
        return [start], info

    open_heap = [(heuristic(start, goal), 0.0, start)]
    g_score = {start: 0.0}
    came_from = {}

    while open_heap:
        _, g_cur, current = heapq.heappop(open_heap)
        if g_cur > g_score.get(current, math.inf) + 1e-12:
            continue
        if current == goal:
            break
        info["expanded"] += 1
        cx, cy = current
        for dx, dy, cost in _NEIGHBORS:
            nx, ny = cx + dx, cy + dy
            if dx != 0 and dy != 0:
                if _diagonal_cuts_corner(grid, cx, cy, dx, dy):
                    info["corner_cuts"] += 1
                    continue
            elif grid.blocked(nx, ny):
                continue
            nxt = (nx, ny)
            tentative = g_cur + cost
            if tentative < g_score.get(nxt, math.inf) - 1e-12:
                g_score[nxt] = tentative
                came_from[nxt] = current
                heapq.heappush(open_heap,
                               (tentative + heuristic(nxt, goal), tentative, nxt))

    if goal not in g_score:
        return [], info

    path = [goal]
    while path[-1] != start:
        path.append(came_from[path[-1]])
    path.reverse()
    info["found"] = True
    info["length"] = _polyline_length(path)
    info["turns"] = _count_turns(path)
    return path, info


def _polyline_length(path):
    total = 0.0
    for (x1, y1), (x2, y2) in zip(path, path[1:]):
        total += math.hypot(x2 - x1, y2 - y1)
    return total


def _count_turns(path):
    """转折点（方向变化点）数：内部点中移动方向发生改变者。"""
    turns = 0
    for i in range(1, len(path) - 1):
        d1 = (path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1])
        d2 = (path[i + 1][0] - path[i][0], path[i + 1][1] - path[i][1])
        if d1 != d2:
            turns += 1
    return turns


# ---------- 视线与平滑 ----------

def line_of_sight(grid, a, b):
    """
    判断中心在格心的线段 a->b 是否合法（Amanatides-Woo 精确栅格遍历）：
      * 线段进入的每个栅格必须自由；
      * 若线段恰好穿过格点（两格同时跨越），则与斜步规则一致：
        斜对角格阻挡，或两个共享边格都阻挡 -> 视为穿角，拒绝。
    单元占据 [x-0.5,x+0.5] x [y-0.5,y+0.5]，格心即整数坐标。
    """
    if a == b:
        return not grid.blocked(a[0], a[1])
    x0, y0 = a
    x1, y1 = b
    if grid.blocked(x0, y0) or grid.blocked(x1, y1):
        return False
    dx, dy = x1 - x0, y1 - y0
    cx, cy = x0, y0

    if dx == 0:
        step_y = 1 if dy > 0 else -1
        for _ in range(abs(dy)):
            cy += step_y
            if grid.blocked(cx, cy):
                return False
        return True
    if dy == 0:
        step_x = 1 if dx > 0 else -1
        for _ in range(abs(dx)):
            cx += step_x
            if grid.blocked(cx, cy):
                return False
        return True

    step_x = 1 if dx > 0 else -1
    step_y = 1 if dy > 0 else -1
    # 参数 t∈[0,1]，到达第一条垂直/水平线及每跨一格的增量
    t_max_x = 0.5 / abs(dx)
    t_max_y = 0.5 / abs(dy)
    t_delta_x = 1.0 / abs(dx)
    t_delta_y = 1.0 / abs(dy)

    while (cx, cy) != (x1, y1):
        if abs(t_max_x - t_max_y) < 1e-12:
            # 恰好穿过格点
            diag = (cx + step_x, cy + step_y)
            side_x = (cx + step_x, cy)
            side_y = (cx, cy + step_y)
            if (grid.blocked(*diag) or
                    (grid.blocked(*side_x) and grid.blocked(*side_y))):
                return False
            cx, cy = diag
            t_max_x += t_delta_x
            t_max_y += t_delta_y
        elif t_max_x < t_max_y:
            cx += step_x
            t_max_x += t_delta_x
            if grid.blocked(cx, cy):
                return False
        else:
            cy += step_y
            t_max_y += t_delta_y
            if grid.blocked(cx, cy):
                return False
    return True


def smooth_path(grid, path):
    """
    贪心视线平滑：从起点不断取视线可达的最远节点作为新拐角点。
    返回 (smoothed, info)，info 含 length / turns / removed。
    """
    if len(path) <= 2:
        smoothed = list(path)
        return smoothed, {"length": _polyline_length(smoothed),
                          "turns": _count_turns(smoothed), "removed": 0}
    smoothed = [path[0]]
    i = 0
    while i < len(path) - 1:
        j = len(path) - 1
        while j > i + 1 and not line_of_sight(grid, path[i], path[j]):
            j -= 1
        smoothed.append(path[j])
        i = j
    info = {
        "length": _polyline_length(smoothed),
        "turns": _count_turns(smoothed),
        "removed": len(path) - len(smoothed),
    }
    return smoothed, info


def validate_path(grid, path):
    """
    路径合法性校验：相邻点必须是八邻域一步、目标格自由、斜步不穿角。
    返回 {"valid": bool, "corner_cuts": int, "reasons": [...]}。
    """
    reasons = []
    corner_cuts = 0
    if not path:
        return {"valid": False, "corner_cuts": 0,
                "reasons": ["空路径"]}
    for p in path:
        if not grid.in_bounds(*p):
            reasons.append(f"越界 {p}")
        elif grid.blocked(*p):
            reasons.append(f"经过障碍 {p}")
    for (x1, y1), (x2, y2) in zip(path, path[1:]):
        ddx, ddy = abs(x2 - x1), abs(y2 - y1)
        if ddx > 1 or ddy > 1 or (ddx == 0 and ddy == 0):
            reasons.append(f"非单步移动 {(x1, y1)}->{(x2, y2)}")
            continue
        if ddx == 1 and ddy == 1:
            # 与 _diagonal_cuts_corner 完全一致的独立判定
            cuts = (grid.blocked(x2, y1) and grid.blocked(x1, y2))
            if cuts:
                corner_cuts += 1
                reasons.append(f"穿角 {(x1, y1)}->{(x2, y2)}")
    return {"valid": not reasons, "corner_cuts": corner_cuts,
            "reasons": reasons}
