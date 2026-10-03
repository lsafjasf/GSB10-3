# -*- coding: utf-8 -*-
"""等值线提取库：Marching Squares（仅依赖 Python 3 标准库）。

标量场约定
----------
``field[j][i]``：j 为行（y 方向，自下而上），i 为列（x 方向，自左而右）。
``field`` 有 ny 行、nx 列，对应 (nx-1)*(ny-1) 个格。

角点编号：0=左下(BL), 1=右下(BR), 2=右上(TR), 3=左上(TL)
边编号  ：0=底边, 1=右边, 2=顶边, 3=左边

高/低于阈值的判定：v >= level 记为“高”（含等号，全库一致）。

歧义消歧
--------
case 5（BL、TR 高）与 case 10（BR、TL 高）有两种线段配对。
采用“渐近线判据”（asymptotic decider）：格内按双线性插值，
中心点值 fc=(BL+BR+TR+TL)/4。
  * fc >= level：两“高角”在格内连通 —— 高-高 配对；
  * fc <  level：两“高角”被等值面隔开 —— 高-高 分离配对。
所有歧义格使用同一规则，保证拓扑一致。
"""

from collections import defaultdict
from dataclasses import dataclass, field
from math import atan2 as math_atan2
from math import cos, pi, sin
from typing import Dict, List, Sequence, Tuple

Point = Tuple[float, float]
Node = Tuple  # ('h'|'v'|'p', ...) 逻辑网格节点键

# 16 种 case 的线段配对（边对）。5、10 为歧义格，见 _resolve_saddle。
_CASES: Dict[int, Tuple[Tuple[int, int], ...]] = {
    0: (), 15: (),
    1: ((3, 0),),        14: ((3, 0),),
    2: ((0, 1),),        13: ((0, 1),),
    3: ((3, 1),),        12: ((3, 1),),
    4: ((1, 2),),        11: ((1, 2),),
    6: ((0, 2),),        9: ((0, 2),),
    7: ((3, 2),),        8: ((3, 2),),
}

_EPS = 1e-12


def _resolve_saddle(case: int, corners: Sequence[float], level: float):
    """歧义格配对，返回 (边对列表, 是否高角连通)。"""
    center = sum(corners) / 4.0
    join_high = center >= level
    if case == 5:   # BL、TR 高
        if join_high:
            return ((0, 1), (2, 3)), True   # 底-右、顶-左：高角连通
        return ((3, 0), (1, 2)), False
    # case 10：BR、TL 高
    if join_high:
        return ((0, 3), (1, 2)), True       # 底-左、右-顶：高角连通
    return ((0, 1), (2, 3)), False


@dataclass
class ContourResult:
    lines: List[List[Point]] = field(default_factory=list)
    closed_count: int = 0
    open_count: int = 0
    stats: Dict[str, int] = field(default_factory=dict)


def _edge_node(edge: int, i: int, j: int, field_grid: Sequence[Sequence[float]],
               level: float, x0: float, y0: float, dx: float, dy: float):
    """返回某格某条边上等值线穿越点的 (逻辑节点键, 坐标)。

    端点落在格点上（插值参数 t≈0 或 1）时把节点键归并为格点键
    ('p', x, y)，避免“同一格点被多条边当作不同节点”而断线。
    """
    g = field_grid
    if edge == 0:   # 底边 BL->BR
        v0, v1 = g[j][i], g[j][i + 1]
        t = (level - v0) / (v1 - v0)
        if t <= _EPS:
            return ("p", i, j), (x0 + i * dx, y0 + j * dy)
        if t >= 1.0 - _EPS:
            return ("p", i + 1, j), (x0 + (i + 1) * dx, y0 + j * dy)
        return ("h", i, j), (x0 + (i + t) * dx, y0 + j * dy)
    if edge == 2:   # 顶边 TL->TR
        v0, v1 = g[j + 1][i], g[j + 1][i + 1]
        t = (level - v0) / (v1 - v0)
        if t <= _EPS:
            return ("p", i, j + 1), (x0 + i * dx, y0 + (j + 1) * dy)
        if t >= 1.0 - _EPS:
            return ("p", i + 1, j + 1), (x0 + (i + 1) * dx, y0 + (j + 1) * dy)
        return ("h", i, j + 1), (x0 + (i + t) * dx, y0 + (j + 1) * dy)
    if edge == 3:   # 左边 BL->TL
        v0, v1 = g[j][i], g[j + 1][i]
        t = (level - v0) / (v1 - v0)
        if t <= _EPS:
            return ("p", i, j), (x0 + i * dx, y0 + j * dy)
        if t >= 1.0 - _EPS:
            return ("p", i, j + 1), (x0 + i * dx, y0 + (j + 1) * dy)
        return ("v", i, j), (x0 + i * dx, y0 + (j + t) * dy)
    # edge == 1：右边 BR->TR
    v0, v1 = g[j][i + 1], g[j + 1][i + 1]
    t = (level - v0) / (v1 - v0)
    if t <= _EPS:
        return ("p", i + 1, j), (x0 + (i + 1) * dx, y0 + j * dy)
    if t >= 1.0 - _EPS:
        return ("p", i + 1, j + 1), (x0 + (i + 1) * dx, y0 + (j + 1) * dy)
    return ("v", i + 1, j), (x0 + (i + 1) * dx, y0 + (j + t) * dy)


def _is_boundary(node: Node, nx: int, ny: int) -> bool:
    kind = node[0]
    if kind == "h":
        return node[2] == 0 or node[2] == ny - 1
    if kind == "v":
        return node[1] == 0 or node[1] == nx - 1
    return node[1] in (0, nx - 1) or node[2] in (0, ny - 1)


def extract_contours(field_grid: Sequence[Sequence[float]], level: float,
                     x0: float = 0.0, y0: float = 0.0,
                     dx: float = 1.0, dy: float = 1.0) -> ContourResult:
    """从标量场提取全部等值线并把逐格线段接成折线。"""
    ny = len(field_grid)
    nx = len(field_grid[0]) if ny else 0
    if nx < 2 or ny < 2:
        raise ValueError("标量场至少需要 2x2 个采样点")
    for row in field_grid:
        if len(row) != nx:
            raise ValueError("标量场各行长度必须一致")

    segments: List[Tuple[Node, Node]] = []
    points: Dict[Node, Point] = {}
    n_ambiguous = n_join = n_separate = n_zero_len = 0

    def node_of(edge: int, i: int, j: int):
        node, pt = _edge_node(edge, i, j, field_grid, level, x0, y0, dx, dy)
        points[node] = pt
        return node

    for j in range(ny - 1):
        for i in range(nx - 1):
            corners = (field_grid[j][i], field_grid[j][i + 1],
                       field_grid[j + 1][i + 1], field_grid[j + 1][i])
            case = sum((1 << k) for k, v in enumerate(corners) if v >= level)
            if case in (5, 10):
                pairs, join_high = _resolve_saddle(case, corners, level)
                n_ambiguous += 1
                if join_high:
                    n_join += 1
                else:
                    n_separate += 1
            else:
                pairs = _CASES[case]
            for ea, eb in pairs:
                a, b = node_of(ea, i, j), node_of(eb, i, j)
                if a == b:
                    n_zero_len += 1
                    continue
                segments.append((a, b))

    # 端口 = (线段号, 端 0/1)。先按逻辑节点归集所有线段端口，
    # 再把同一节点上的端口配成“连接”，沿连接即可把线段拼成折线。
    ports_at: Dict[Node, List[Tuple[int, int]]] = defaultdict(list)
    for sid, (a, b) in enumerate(segments):
        ports_at[a].append((sid, 0))
        ports_at[b].append((sid, 1))

    links: Dict[Tuple[int, int], Tuple[int, int]] = {}

    def link(p: Tuple[int, int], q: Tuple[int, int]) -> None:
        links[p] = q
        links[q] = p

    def bearing(port: Tuple[int, int], node: Node):
        sid, end = port
        other = segments[sid][1 - end]
        ox, oy = points[other]
        px, py = points[node]
        return math_atan2(oy - py, ox - px)

    breaks = 0
    for node, ports in ports_at.items():
        deg = len(ports)
        boundary = _is_boundary(node, nx, ny)
        if deg == 2:
            link(ports[0], ports[1])
        elif deg == 4 and node[0] == "p":
            # 等值线恰好穿过格点：按绕该点的方位角循环配对，
            # 避免把在此相会的两条等值线交叉错连。
            order = sorted(ports, key=lambda p: bearing(p, node))
            link(order[0], order[1])
            link(order[2], order[3])
        elif deg == 1 and boundary:
            pass  # 开放等值线的合法边界端点
        else:
            # 内部悬空端（度 1）或无法合法配对的多重汇合（度 3、度>4）= 断点
            breaks += 1 if deg == 1 else deg

    def trace(sid0: int, end0: int):
        """从 (sid0, end0) 端口出发，沿 links 拼成折线。"""
        pts = [points[segments[sid0][end0]]]
        used[sid0] = True
        sid, end = sid0, end0
        while True:
            other = 1 - end
            pts.append(points[segments[sid][other]])
            nxt = links.get((sid, other))
            if nxt is None:
                return pts, False                # 开放线
            if nxt == (sid0, end0):
                return pts[:-1], True            # 闭合：去掉重复首点
            sid, end = nxt
            used[sid] = True

    used = [False] * len(segments)
    closed_lines: List[List[Point]] = []
    open_lines: List[List[Point]] = []

    # 开放线：从未建立连接的边界端口出发
    dangling = {(sid, e) for sid in range(len(segments))
                for e in (0, 1) if (sid, e) not in links}
    for port in sorted(dangling):
        sid = port[0]
        if not used[sid]:
            line, _ = trace(sid, port[1])
            open_lines.append(line)

    # 闭合线：剩余线段全成环
    for sid in range(len(segments)):
        if not used[sid]:
            line, is_closed = trace(sid, 0)
            assert is_closed
            closed_lines.append(line)

    result = ContourResult(
        lines=closed_lines + open_lines,
        closed_count=len(closed_lines),
        open_count=len(open_lines),
        stats={
            "cells": (nx - 1) * (ny - 1),
            "segments": len(segments),
            "ambiguous_cells": n_ambiguous,
            "ambiguous_join_high": n_join,
            "ambiguous_separate_high": n_separate,
            "zero_length_segments_dropped": n_zero_len,
            "break_points": breaks,
        },
    )
    return result


def assert_connected(result: ContourResult) -> None:
    """连接性断言：断点数必须为零，且每条线段恰被使用一次。"""
    assert result.stats["break_points"] == 0, (
        "存在 %d 个断点，相邻格线段未首尾相接" % result.stats["break_points"])
    # 闭合线 n 个顶点对应 n 条线段；开放线 n 个顶点对应 n-1 条
    used_segments = 0
    for idx, line in enumerate(result.lines):
        used_segments += len(line) if idx < result.closed_count else len(line) - 1
    assert used_segments == result.stats["segments"], (
        "线段使用数 %d != 生成数 %d" % (used_segments, result.stats["segments"]))


def _demo() -> None:
    n = 21
    field_grid = [
        [sin(2.0 * pi * i / (n - 1)) * cos(2.0 * pi * j / (n - 1))
         for i in range(n)]
        for j in range(n)
    ]
    level = 0.0
    r = extract_contours(field_grid, level)
    assert_connected(r)
    print("演示场：f(i,j)=sin(x)*cos(y)，21x21，阈值 %.1f" % level)
    print("总格数          :", r.stats["cells"])
    print("生成线段数       :", r.stats["segments"])
    print("歧义格数(case5/10):", r.stats["ambiguous_cells"],
          "  高角连通:", r.stats["ambiguous_join_high"],
          " 高角分离:", r.stats["ambiguous_separate_high"])
    print("断点数          :", r.stats["break_points"], "(断言为零)")
    print("闭合等值线条数   :", r.closed_count)
    print("开放等值线条数   :", r.open_count)


if __name__ == "__main__":
    _demo()
