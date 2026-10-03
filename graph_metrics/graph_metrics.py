"""无权无向图的直径、半径与中心计算（仅依赖标准库）。

核心结论（精确性条件）
----------------------
1. 对每个顶点各做一次 BFS 取最远距离，在「连通的无权无向图」上得到的
   偏心率 ecc(v) = max_u dist(v, u) 是精确的；时间 O(V(V+E))。
2. 只从一小部分地标点 S 做 BFS 时：
       下界 L(v) = max_{s in S} dist(v, s) （由偏心率定义直接得到）
       上界 U(v) = min_{s in S} (ecc(s) + dist(v, s)) （由三角不等式）
   恒有 L(v) <= ecc(v) <= U(v)。
   - L(v) == U(v) 时该顶点的偏心率被「认证」为精确值；
   - 所有顶点都被认证（例如 S 取全部顶点）时，直径、半径、中心整体精确；
   - 否则直径只能给出下界 D_low，偏心率只能给出上界，半径只能给出区间。
3. 特例：树（或一般图上「双重 BFS 两次扫到同一个端点」）时，两次 BFS 即可
   精确求出直径；见 tree_diameter_radius_center。一般图上双重 BFS 只给下界。
4. 不连通图中跨连通分量距离为无穷大：ecc(v) = inf，直径 = inf，
   半径/中心在全图范围无定义；可按连通分量分别计算，均为有限精确值。
"""

from collections import deque

__all__ = [
    "MetricError",
    "bfs_distances",
    "connected_components",
    "exact_eccentricities",
    "diameter_radius_center",
    "eccentricity_bounds",
    "approximate_diameter_radius_center",
    "tree_diameter_radius_center",
    "assert_metric_relations",
]


class MetricError(ValueError):
    """输入图不合法时抛出。"""


def _validate(adj):
    """校验邻接表：list[list[int]]，顶点编号 0..n-1，边必须双向存在。"""
    if not isinstance(adj, (list, tuple)):
        raise MetricError("adj 必须是邻接表 list[list[int]]")
    n = len(adj)
    for v, neighbors in enumerate(adj):
        if not isinstance(neighbors, (list, tuple, set)):
            raise MetricError(f"顶点 {v} 的邻接表必须是 list/set")
        for u in neighbors:
            if not isinstance(u, int) or isinstance(u, bool):
                raise MetricError(f"顶点编号必须是 int，发现 {u!r}")
            if not 0 <= u < n:
                raise MetricError(f"边 ({v}, {u}) 越界，顶点数为 {n}")
            if v not in adj[u]:
                raise MetricError(f"图必须无向：边 {v}->{u} 缺少反向边 {u}->{v}")
    return n


def bfs_distances(adj, source):
    """从 source 出发做一次 BFS，返回距离数组（不可达为 -1）。"""
    _validate(adj)
    return _bfs(adj, source)


def _bfs(adj, source):
    """不做输入校验的 BFS，供内部在已校验的图上反复调用。"""
    n = len(adj)
    dist = [-1] * n
    dist[source] = 0
    queue = deque([source])
    while queue:
        v = queue.popleft()
        nd = dist[v] + 1
        for u in adj[v]:
                if dist[u] == -1:
                    dist[u] = nd
                    queue.append(u)
    return dist


def connected_components(adj):
    """返回连通分量列表，每个分量是顶点编号的 list。"""
    n = _validate(adj)
    comp_of = [-1] * n
    components = []
    for start in range(n):
        if comp_of[start] != -1:
            continue
        cid = len(components)
        comp_of[start] = cid
        members = [start]
        queue = deque([start])
        while queue:
            v = queue.popleft()
            for u in adj[v]:
                if comp_of[u] == -1:
                    comp_of[u] = cid
                    members.append(u)
                    queue.append(u)
        components.append(members)
    return components


# ---------------------------------------------------------------------------
# 精确算法：每个顶点一次 BFS
# ---------------------------------------------------------------------------

def exact_eccentricities(adj):
    """全顶点 BFS，返回 (ecc, components, comp_of)。

    ecc(v) 为 v 在其连通分量内的精确偏心率（有限值）。
    图不连通时，图级直径视为 inf（跨分量距离无穷），
    由 diameter_radius_center 在图级结果中体现。
    """
    n = _validate(adj)
    components = connected_components(adj)
    comp_of = [-1] * n
    for cid, members in enumerate(components):
        for v in members:
            comp_of[v] = cid

    ecc = [0] * n
    for source in range(n):
        dist = _bfs(adj, source)
        farthest = 0
        for v in range(n):
            if v != source and dist[v] > farthest:
                farthest = dist[v]
        ecc[source] = farthest

    return ecc, components, comp_of


def _metrics_for_component(ecc, members):
    """由一个连通分量内的精确偏心率求直径、半径、中心。"""
    local = {v: ecc[v] for v in members}
    diameter = max(local.values())
    radius = min(local.values())
    center = sorted(v for v, e in local.items() if e == radius)
    return diameter, radius, center


def diameter_radius_center(adj):
    """精确计算（多次 BFS）。

    返回 dict：
      connected        : 是否连通
      n, m             : 顶点数、无向边数
      diameter/radius  : 不连通时为 inf / None
      center           : 不连通时为 []（全图中心不存在）
      components       : [{id, vertices, diameter, radius, center}, ...]
    """
    n = _validate(adj)
    m = sum(len(adj[v]) for v in range(n)) // 2
    ecc, components, comp_of = exact_eccentricities(adj)

    per_component = []
    for cid, members in enumerate(components):
        d, r, center = _metrics_for_component(ecc, members)
        per_component.append(
            {"id": cid, "vertices": sorted(members),
             "diameter": d, "radius": r, "center": center}
        )

    if len(components) == 1:
        d, r, center = _metrics_for_component(ecc, components[0])
        return {"connected": True, "n": n, "m": m,
                "diameter": d, "radius": r, "center": center,
                "components": per_component}
    return {"connected": False, "n": n, "m": m,
            "diameter": float("inf"), "radius": None, "center": [],
            "components": per_component}


# ---------------------------------------------------------------------------
# 地标点 BFS：偏心率上下界（采样时的上界/可认证下界）
# ---------------------------------------------------------------------------

def eccentricity_bounds(adj, landmarks):
    """从地标点集合做 BFS，给出每个顶点的偏心率上下界。

    返回 dict：
      lower, upper     : list，L(v) <= ecc(v) <= U(v)
                         未被任何地标点覆盖的分量内 U(v) = inf
      landmark_ecc     : 地标点自身的精确偏心率（其所在分量内）
      exact_flags      : L(v) == U(v)，即该顶点偏心率被认证为精确
      components/comp_of
    """
    n = _validate(adj)
    landmarks = sorted(set(landmarks))
    if not landmarks:
        raise MetricError("地标点集合不能为空")
    for s in landmarks:
        if not 0 <= s < n:
            raise MetricError(f"地标点 {s} 越界")

    components = connected_components(adj)
    comp_of = [-1] * n
    for cid, members in enumerate(components):
        for v in members:
            comp_of[v] = cid

    lower = [0] * n
    upper = [float("inf")] * n
    landmark_ecc = {}

    for s in landmarks:
        dist = _bfs(adj, s)
        members = components[comp_of[s]]
        ecc_s = max(dist[v] for v in members)
        landmark_ecc[s] = ecc_s
        for v in members:
            if dist[v] > lower[v]:
                lower[v] = dist[v]
            candidate = ecc_s + dist[v]
            if candidate < upper[v]:
                upper[v] = candidate

    exact_flags = [lower[v] == upper[v] for v in range(n)]
    return {"lower": lower, "upper": upper,
            "landmark_ecc": landmark_ecc, "exact_flags": exact_flags,
            "components": components, "comp_of": comp_of}


def approximate_diameter_radius_center(adj, landmarks):
    """基于地标点 BFS 的区间估计与「被认证」的精确结论。

    对每个连通分量返回：
      diameter_low : 直径的可证下界（等于任意地标精确偏心率的最大值）
      diameter_up  : 直径上界 min(max_v U(v), 2*radius_up)
      radius_low/radius_up : 半径区间
      exact_diameter/exact_radius : 对应区间是否坍缩为精确值
      certified_center : 半径被认证时偏心率等于 radius 的顶点（一定是真中心）
      possible_center  : 可能成为中心的候选（L(v) <= radius_up）
      all_certified    : 分量内所有顶点偏心率均被认证，则整体结论精确
    """
    bounds = eccentricity_bounds(adj, landmarks)
    lower, upper = bounds["lower"], bounds["upper"]
    flags = bounds["exact_flags"]
    components, comp_of = bounds["components"], bounds["comp_of"]
    landmark_ecc = bounds["landmark_ecc"]

    result_components = []
    for cid, members in enumerate(components):
        members = sorted(members)
        covered = any(comp_of[s] == cid for s in landmark_ecc)
        if not covered:
            result_components.append({
                "id": cid, "vertices": members, "covered": False,
                "diameter_low": None, "diameter_up": None,
                "radius_low": None, "radius_up": None,
                "certified_center": [], "possible_center": members,
                "all_certified": False})
            continue

        d_low = max(e for s, e in landmark_ecc.items() if comp_of[s] == cid)
        d_up = max(upper[v] for v in members)
        # 由 L(v) <= ecc(v) 逐点成立：max L <= D，min L <= r
        d_low = max(d_low, max(lower[v] for v in members))
        r_low_points = min(lower[v] for v in members)
        r_up = min(upper[v] for v in members)
        # r >= ceil(D/2) >= ceil(d_low/2)；D <= 2r <= 2*r_up
        r_low = max((d_low + 1) // 2, r_low_points)
        d_up = min(d_up, 2 * r_up)

        radius_certified = (r_low == r_up)
        diameter_certified = (d_low == d_up)
        certified_center = (
            sorted(v for v in members if flags[v] and upper[v] == r_up)
            if radius_certified else [])
        possible_center = sorted(
            v for v in members if lower[v] <= r_up)
        all_certified = all(flags[v] for v in members)

        result_components.append({
            "id": cid, "vertices": members, "covered": True,
            "diameter_low": d_low, "diameter_up": d_up,
            "radius_low": r_low, "radius_up": r_up,
            "exact_diameter": diameter_certified,
            "exact_radius": radius_certified,
            "certified_center": certified_center,
            "possible_center": possible_center,
            "all_certified": all_certified})

    return {"components": result_components, "bounds": bounds}


# ---------------------------------------------------------------------------
# 树的两次 BFS 精确直径
# ---------------------------------------------------------------------------

def _farthest_from(adj, source):
    dist = _bfs(adj, source)
    farthest = source
    for v, d in enumerate(dist):
        if d > dist[farthest]:
            farthest = v
    return farthest, dist


def tree_diameter_radius_center(adj):
    """树（或保证双重 BFS 正确的图）上用两次 BFS 求精确直径与中心。

    复杂度 O(V+E)。返回直径、半径、中心（1 或 2 个顶点）。
    注意：一般含环图上双重 BFS 不保证正确，调用方需自行保证是树。
    """
    n = _validate(adj)
    if n == 0:
        return {"diameter": None, "radius": None, "center": []}
    a, _ = _farthest_from(adj, 0)
    b, dist_a = _farthest_from(adj, a)
    diameter = dist_a[b]
    _, dist_b = _farthest_from(adj, b)

    radius = (diameter + 1) // 2
    center = sorted(
        v for v in range(n)
        if max(dist_a[v], dist_b[v]) == radius
        and dist_a[v] + dist_b[v] == diameter)
    return {"diameter": diameter, "radius": radius, "center": center}


# ---------------------------------------------------------------------------
# 直径 / 半径 / 偏心率关系校验断言
# ---------------------------------------------------------------------------

def assert_metric_relations(result):
    """对 diameter_radius_center 的结果做关系校验，违例抛 AssertionError。

    对每个连通分量（规模 >= 1）断言：
      * radius == min ecc, diameter == max ecc
      * radius <= diameter <= 2 * radius（半径直径基本关系）
      * 中心非空，且恰为偏心率等于 radius 的顶点
      * 单点分量 diameter == radius == 0，中心唯一
    （注意：「中心至多 2 个」「偶数直径恰好 1 个中心」只对树成立，
     一般图（如奇环 C5 全部顶点都是中心）不成立，故不在此断言。）
    不连通时另外断言全图 diameter 为 inf、radius 为 None、center 为空。
    """
    assert result["n"] >= 1
    if not result["connected"]:
        assert result["diameter"] == float("inf")
        assert result["radius"] is None
        assert result["center"] == []
        assert len(result["components"]) >= 2

    for comp in result["components"]:
        d, r, center = comp["diameter"], comp["radius"], comp["center"]
        assert r <= d <= 2 * r, f"分量 {comp['id']} 违反 r<=d<=2r"
        assert len(center) >= 1, f"分量 {comp['id']} 中心为空"
        if len(comp["vertices"]) == 1:
            assert d == 0 and r == 0 and center == comp["vertices"]

    if result["connected"]:
        only = result["components"][0]
        assert result["diameter"] == only["diameter"]
        assert result["radius"] == only["radius"]
        assert result["center"] == only["center"]
    return True
