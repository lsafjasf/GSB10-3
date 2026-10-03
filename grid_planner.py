"""栅格路径规划库（仅依赖 Python 3 标准库）。

特性：
- A* 搜索，启发函数为 octile 距离（对 8 邻接、对角代价 sqrt(2) 可采纳且一致）。
- 对角移动禁止穿越障碍角（no corner cutting）。
- 基于 Amanatides-Woo 网格射线遍历的视线检查（supercover），
  线段擦过障碍角即视为不可通行，规则与 A* 走步严格一致。
- 贪心“拉绳”路径平滑，给出平滑前后的转折点数与路径长度。

坐标约定：cell = (row, col)，row 向下、col 向右，均为 0 基整数。
"""

import heapq
import math
from fractions import Fraction

SQRT2 = math.sqrt(2.0)

# 8 邻接：正交代价 1，对角代价 sqrt(2)
MOVES = (
    (-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
    (-1, -1, SQRT2), (-1, 1, SQRT2), (1, -1, SQRT2), (1, 1, SQRT2),
)

_TOL = 1e-12
_INF_T = Fraction(10 ** 30)


def octile(a, b):
    """octile 距离：8 邻接 + 对角代价 sqrt(2) 下的可采纳启发函数。

    h = max(dx, dy) + (sqrt(2) - 1) * min(dx, dy)
    恰好等于“完全无障”时 a 到 b 的最短路径代价，故 h <= 带障碍时的真实最优代价。
    """
    dx = abs(a[0] - b[0])
    dy = abs(a[1] - b[1])
    return max(dx, dy) + (SQRT2 - 1.0) * min(dx, dy)


class Grid:
    """矩形栅格地图，blocked 为不可通行 cell 集合。"""

    def __init__(self, rows, cols, blocked=()):
        if rows <= 0 or cols <= 0:
            raise ValueError("rows/cols 必须为正整数")
        self.rows = rows
        self.cols = cols
        self.blocked = frozenset(blocked)
        for (r, c) in self.blocked:
            if not (0 <= r < rows and 0 <= c < cols):
                raise ValueError("障碍越界: %r" % ((r, c),))

    @classmethod
    def from_lines(cls, lines, wall_chars="#@"):
        """从字符行构建地图；wall_chars 中的字符视为障碍。"""
        rows = [line.rstrip("\n") for line in lines if line.strip("\n") != ""]
        if not rows:
            raise ValueError("地图为空")
        width = len(rows[0])
        if any(len(line) != width for line in rows):
            raise ValueError("地图各行宽度不一致")
        blocked = set()
        for r, line in enumerate(rows):
            for c, ch in enumerate(line):
                if ch in wall_chars:
                    blocked.add((r, c))
        return cls(len(rows), width, blocked)

    def in_bounds(self, cell):
        return 0 <= cell[0] < self.rows and 0 <= cell[1] < self.cols

    def is_free(self, cell):
        return self.in_bounds(cell) and cell not in self.blocked

    def neighbors(self, cell):
        """可行走邻居；对角移动要求两侧正交格均可通行（禁止穿角）。"""
        r, c = cell
        for dr, dc, cost in MOVES:
            nb = (r + dr, c + dc)
            if not self.is_free(nb):
                continue
            if dr != 0 and dc != 0:
                if not self.is_free((r + dr, c)) or not self.is_free((r, c + dc)):
                    continue  # 穿越障碍角，禁止
            yield nb, cost


def astar(grid, start, goal):
    """A* 搜索。返回 (path, cost)；无解返回 (None, inf)。

    path 为含 start/goal 的 cell 列表。octile 一致，首次弹出 goal 即最优。
    """
    if not grid.is_free(start):
        raise ValueError("起点不可通行: %r" % (start,))
    if not grid.is_free(goal):
        raise ValueError("终点不可通行: %r" % (goal,))
    g_score = {start: 0.0}
    came = {start: None}
    heap = [(octile(start, goal), 0.0, 0, start)]
    tick = 0
    closed = set()
    while heap:
        _, gc, _, cur = heapq.heappop(heap)
        if cur in closed:
            continue
        closed.add(cur)
        if cur == goal:
            path = []
            node = cur
            while node is not None:
                path.append(node)
                node = came[node]
            path.reverse()
            return path, gc
        for nb, cost in grid.neighbors(cur):
            ng = gc + cost
            if ng < g_score.get(nb, math.inf) - _TOL:
                g_score[nb] = ng
                came[nb] = cur
                tick += 1
                heapq.heappush(heap, (ng + octile(nb, goal), ng, tick, nb))
    return None, math.inf


def dijkstra_dist(grid, src):
    """从 src 出发的精确最短距离（同走步规则），用于验证启发函数可采纳性。"""
    dist = {src: 0.0}
    heap = [(0.0, src)]
    while heap:
        d, cur = heapq.heappop(heap)
        if d > dist.get(cur, math.inf) + _TOL:
            continue
        for nb, cost in grid.neighbors(cur):
            nd = d + cost
            if nd < dist.get(nb, math.inf) - _TOL:
                dist[nb] = nd
                heapq.heappush(heap, (nd, nb))
    return dist


def path_length(path):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(path, path[1:]))


def count_turns(path):
    """转折点数：行进方向发生变化的中间顶点数。"""
    turns = 0
    for i in range(1, len(path) - 1):
        d1 = (path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1])
        d2 = (path[i + 1][0] - path[i][0], path[i + 1][1] - path[i][1])
        if d1 != d2:
            turns += 1
    return turns


def count_step_corner_cuts(grid, path):
    """逐步路径中的穿角次数：对角步的任一侧正交格被挡即计一次。"""
    cuts = 0
    for a, b in zip(path, path[1:]):
        dr, dc = b[0] - a[0], b[1] - a[1]
        if dr != 0 and dc != 0:
            if (not grid.is_free((a[0] + dr, a[1]))
                    or not grid.is_free((a[0], a[1] + dc))):
                cuts += 1
    return cuts


def segment_issues(grid, p, q):
    """检查 cell 中心连线 p->q。

    返回 (blocked_hits, corner_cuts)：
    - blocked_hits：线段进入的障碍格数（起点格若为障碍也计入）；
    - corner_cuts：线段恰好擦过格点（角）、且该点相邻的两个正交格
      至少一个为障碍的次数；这种情况不计入 blocked_hits。
    用 Amanatides-Woo 网格遍历 + Fraction 精确比较，相切判定无浮点误差。
    """
    if p == q:
        return (0 if grid.is_free(p) else 1), 0

    nr, nc = q[0] - p[0], q[1] - p[1]
    ar, ac = abs(nr), abs(nc)
    sr, sc = (nr > 0) - (nr < 0), (nc > 0) - (nc < 0)
    r, c = p

    kr, kc = 1, 1  # 下一个穿越的是各自方向的第几条网格线
    tr = Fraction(kr, ar) if ar else _INF_T
    tc = Fraction(kc, ac) if ac else _INF_T

    blocked_hits = 0 if grid.is_free((r, c)) else 1
    corner_cuts = 0
    while tr <= 1 or tc <= 1:
        if tr < tc:
            r += sr
            kr += 1
            tr = Fraction(kr, ar) if kr <= ar else _INF_T
            if not grid.is_free((r, c)):
                blocked_hits += 1
        elif tc < tr:
            c += sc
            kc += 1
            tc = Fraction(kc, ac) if kc <= ac else _INF_T
            if not grid.is_free((r, c)):
                blocked_hits += 1
        else:
            # 同时穿过一条行线和列线：精确经过某个格点（角）
            side_r = (r + sr, c)
            side_c = (r, c + sc)
            diag = (r + sr, c + sc)
            r += sr
            c += sc
            kr += 1
            kc += 1
            tr = Fraction(kr, ar) if kr <= ar else _INF_T
            tc = Fraction(kc, ac) if kc <= ac else _INF_T
            if not grid.is_free(side_r) or not grid.is_free(side_c):
                corner_cuts += 1
            elif not grid.is_free(diag):
                blocked_hits += 1
    return blocked_hits, corner_cuts


def has_line_of_sight(grid, p, q):
    """p 到 q 可直线通行：不进障碍格，也不擦障碍角。"""
    blocked_hits, corner_cuts = segment_issues(grid, p, q)
    return blocked_hits == 0 and corner_cuts == 0


def count_path_issues(grid, path):
    """统计整条路径（可为平滑后的折线）的穿格/穿角总数。"""
    blocked_hits = 0
    corner_cuts = 0
    for a, b in zip(path, path[1:]):
        bh, cc = segment_issues(grid, a, b)
        blocked_hits += bh
        corner_cuts += cc
    return blocked_hits, corner_cuts


def smooth_path(grid, path):
    """贪心拉绳平滑：每一步从当前点跳到视线可达的最远路径点。"""
    if len(path) <= 2:
        return list(path)
    smoothed = [path[0]]
    i = 0
    n = len(path)
    while i < n - 1:
        j = n - 1
        while j > i + 1 and not has_line_of_sight(grid, path[i], path[j]):
            j -= 1
        smoothed.append(path[j])
        i = j
    return smoothed


def render(grid, path=()):
    """字符画：# 障碍，. 空地，* 路径，S/G 起终点。"""
    canvas = [["#" if (r, c) in grid.blocked else "." for c in range(grid.cols)]
              for r in range(grid.rows)]
    for cell in path:
        canvas[cell[0]][cell[1]] = "*"
    if path:
        canvas[path[0][0]][path[0][1]] = "S"
        canvas[path[-1][0]][path[-1][1]] = "G"
    return "\n".join("".join(row) for row in canvas)
