"""多边形自交检测与修复建议（纯标准库）。

特性：
- 精确算术：所有坐标转为 fractions.Fraction，方向/相交判定无浮点误差。
- 检测任意两条非相邻边的相交：真交叉(cross)、端点触碰(touch)、共线重叠(overlap)。
- 退化处理：重复点、零长度边在归一化阶段剔除并记录，不产生误报；
  相邻边共享端点天然跳过；少于 3 个有效顶点的输入安全返回。
"""

from fractions import Fraction


# ---------------------------------------------------------------- 基础几何

def _exact(point):
    """把 (x, y) 转成精确坐标。支持 int / float / Fraction / 数值字符串。"""
    x, y = point
    return (Fraction(x), Fraction(y))


def _orient(a, b, c):
    """向量 AB 与 AC 的叉积（精确）。>0 左转，<0 右转，=0 共线。"""
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a, b, p):
    """p 与 a,b 共线时，判断 p 是否落在闭区间 ab 上。"""
    return (min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
            and min(a[1], b[1]) <= p[1] <= max(a[1], b[1]))


def _line_cross_point(a, b, c, d):
    """直线 ab 与 cd 的真交点（前提：两线段严格相交，非共线）。精确解。"""
    denominator = _orient(a, b, c) - _orient(a, b, d)
    # t = orient(a,b,c) / (orient(a,b,c) - orient(a,b,d))
    t = _orient(a, b, c) / denominator
    return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))


def _overlap_interval(a, b, c, d):
    """两条共线线段的重叠区间端点（可能退化为一个点或为空）。"""
    # 投影到变化量大的轴上比较，避免垂直/水平分别处理
    if a[0] != b[0]:
        key = lambda p: p[0]
    else:
        key = lambda p: p[1]
    seg1 = sorted([a, b], key=key)
    seg2 = sorted([c, d], key=key)
    lo = max(seg1[0], seg2[0], key=key)
    hi = min(seg1[1], seg2[1], key=key)
    if key(lo) > key(hi):
        return None
    return (lo, hi)


def segment_intersection(a, b, c, d):
    """两条闭线段的相交结果。

    返回 None（不相交）或 dict：
      kind   : "cross" | "touch" | "overlap"
      points : cross/touch 为 [点]；overlap 为 [起点, 终点]（可能两点相同）
    """
    d1 = _orient(c, d, a)
    d2 = _orient(c, d, b)
    d3 = _orient(a, b, c)
    d4 = _orient(a, b, d)

    if d1 == 0 and d2 == 0 and d3 == 0 and d4 == 0:
        interval = _overlap_interval(a, b, c, d)
        if interval is None:
            return None
        lo, hi = interval
        if lo == hi:
            return {"kind": "touch", "points": [lo]}
        return {"kind": "overlap", "points": [lo, hi]}

    def opposite(x, y):
        return (x < 0 and y > 0) or (x > 0 and y < 0)

    if opposite(d1, d2) and opposite(d3, d4):
        return {"kind": "cross", "points": [_line_cross_point(a, b, c, d)]}

    points = []
    if d1 == 0 and _on_segment(c, d, a):
        points.append(a)
    if d2 == 0 and _on_segment(c, d, b):
        points.append(b)
    if d3 == 0 and _on_segment(a, b, c):
        points.append(c)
    if d4 == 0 and _on_segment(a, b, d):
        points.append(d)
    points = sorted(set(points))
    if not points:
        return None
    return {"kind": "touch", "points": points}


# ---------------------------------------------------------------- 归一化

def normalize_polygon(points):
    """剔除连续重复点（含首尾闭合重复），即零长度边。

    返回 (vertices, removed)，removed 为被剔除点的原始下标列表。
    """
    raw = [_exact(p) for p in points]
    kept, removed = [], []
    for index, p in enumerate(raw):
        if kept and p == kept[-1]:
            removed.append(index)  # 连续重复：零长度边
        else:
            kept.append(p)
    # 闭合重复：末点 == 首点
    if len(kept) > 1 and kept[-1] == kept[0]:
        removed.append(len(raw) - 1)
        kept.pop()
    vertices = kept
    return vertices, removed


def _edges(vertices):
    n = len(vertices)
    return [(vertices[i], vertices[(i + 1) % n]) for i in range(n)]


def _adjacent(i, j, n):
    return j == i + 1 or (i == 0 and j == n - 1)


# ---------------------------------------------------------------- 主入口

def find_self_intersections(points):
    """检测多边形自交。

    参数 points: [(x, y), ...]，首尾点可重复也可不重复。
    返回 dict：
      vertices            归一化后的顶点（精确坐标）
      removed_duplicates  被剔除的重复点原始下标
      degenerate          退化信息（有效顶点数、是否过少等）
      intersections       相交边对清单，每项：
                          {edges: (i, j), kind, points}
      point_count         去重后的相交点数量
    """
    vertices, removed = normalize_polygon(points)
    n = len(vertices)
    result = {
        "vertices": vertices,
        "removed_duplicates": removed,
        "degenerate": {
            "input_count": len(points),
            "vertex_count": n,
            "too_few_vertices": n < 3,
        },
        "intersections": [],
        "point_count": 0,
    }
    if n < 3:
        return result

    edges = _edges(vertices)
    distinct_points = set()
    for i in range(n):
        for j in range(i + 1, n):
            if _adjacent(i, j, n):
                continue  # 相邻边共享端点，跳过，避免误报
            hit = segment_intersection(*edges[i], *edges[j])
            if hit is None:
                continue
            result["intersections"].append({
                "edges": (i, j),
                "kind": hit["kind"],
                "points": hit["points"],
            })
            distinct_points.update(hit["points"])
    result["point_count"] = len(distinct_points)
    return result


def suggest_repairs(points):
    """基于检测结果给出修复建议（字符串列表，含具体坐标与边下标）。"""
    result = find_self_intersections(points)
    suggestions = []

    if result["removed_duplicates"]:
        suggestions.append(
            "删除连续重复顶点（零长度边），原始下标: "
            + ", ".join(map(str, result["removed_duplicates"])))

    if result["degenerate"]["too_few_vertices"]:
        suggestions.append(
            "有效顶点不足 3 个，无法构成多边形，请检查数据源。")
        return suggestions

    for item in result["intersections"]:
        i, j = item["edges"]
        if item["kind"] == "overlap":
            lo, hi = item["points"]
            suggestions.append(
                "边 %d 与边 %d 共线重叠（区间 %s -> %s）："
                "合并重叠段，或删除回折（spike）子路径。"
                % (i, j, _fmt(lo), _fmt(hi)))
        else:
            for p in item["points"]:
                suggestions.append(
                    "边 %d 与边 %d 在 %s 相%s："
                    "在两条边上插入该交点作为新顶点，"
                    "随后按绕向重连可拆分为简单多边形。"
                    % (i, j, _fmt(p),
                       "交" if item["kind"] == "cross" else "触"))
    if not suggestions:
        suggestions.append("未发现问题，多边形为简单多边形。")
    return suggestions


def _fmt(p):
    def f(v):
        return str(float(v)) if v.denominator != 1 else str(v.numerator)
    return "(%s, %s)" % (f(p[0]), f(p[1]))
