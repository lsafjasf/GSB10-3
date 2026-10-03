"""Diameter, radius and center of an unweighted undirected graph.

All algorithms use BFS, which is exact for shortest-path distances in an
unweighted graph.  Two modes are provided:

* ``exact_diameter`` runs one BFS from every node.  Cost O(n * (n + m));
  the answer is always exact.

* ``estimate_diameter`` runs only a few BFSs (double sweep + midpoint
  probes).  Cost O(k * (n + m)).  Each BFS eccentricity is itself exact;
  the bounds come from looking at only a sample S of nodes:

      max_{v in S} ecc(v) <= diameter <= 2 * min_{v in S} ecc(v)
      radius <= min_{v in S} ecc(v)

  i.e. the sweep value of the diameter is a *lower bound*, and the
  radius value is an *upper bound*.  The estimate is provably exact when
  the sampled nodes include a peripheral node (an endpoint of a farthest
  pair) and a center node:

    - Trees (and forests): the classic two-BFS sweep from any node hits a
      peripheral endpoint, and the midpoint of that path is a center, so
      diameter, radius and center are all exact.
    - General graphs: no guarantee.  Extra sweeps from path midpoints
      usually close the gap in practice, but the result stays a bound
      until cross-checked with ``exact_diameter``.

Disconnected graphs follow the standard graph-theory convention:
diameter and radius are infinite and there is no global center; per
component results are included for inspection.
"""

from collections import deque
from dataclasses import dataclass, field
import math
import random


@dataclass(frozen=True)
class ComponentStats:
    diameter: int
    radius: int
    center: frozenset
    peripheral: frozenset


@dataclass(frozen=True)
class GraphStats:
    connected: bool
    diameter: float          # math.inf when disconnected
    radius: float            # math.inf when disconnected
    center: frozenset        # empty when disconnected
    exact: bool
    components: tuple = field(default_factory=tuple)  # tuple[ComponentStats]

    def summary(self):
        if self.connected:
            kind = "exact" if self.exact else "bounded estimate"
            return (f"diameter={self.diameter} radius={self.radius} "
                    f"center={sorted(self.center)} ({kind})")
        return (f"disconnected graph; {len(self.components)} components; "
                f"diameter=inf radius=inf; largest component: "
                f"{self.components[0].diameter}")


def normalize_adj(adj):
    """Return a dict adjacency view containing every node as a key."""
    nodes = set(adj)
    for u in list(adj):
        nodes.update(adj[u])
    return {u: tuple(adj.get(u, ())) for u in nodes}


def bfs(adj, source):
    """Exact BFS from ``source``.

    Returns (eccentricity, farthest_node, dist, parent).
    """
    dist = {source: 0}
    parent = {source: None}
    queue = deque([source])
    farthest = source
    while queue:
        u = queue.popleft()
        if dist[u] > dist[farthest]:
            farthest = u
        for v in adj[u]:
            if v not in dist:
                dist[v] = dist[u] + 1
                parent[v] = u
                queue.append(v)
    return dist[farthest], farthest, dist, parent


def connected_components(adj):
    seen = set()
    for start in adj:
        if start in seen:
            continue
        comp = set()
        queue = deque([start])
        seen.add(start)
        while queue:
            u = queue.popleft()
            comp.add(u)
            for v in adj[u]:
                if v not in seen:
                    seen.add(v)
                    queue.append(v)
        yield comp


def _path_to(parent, target):
    path = []
    while target is not None:
        path.append(target)
        target = parent[target]
    path.reverse()
    return path


def _component_exact(adj, comp):
    ecc = {}
    for s in comp:
        e, _, _, _ = bfs(adj, s)
        ecc[s] = e
    diameter = max(ecc.values())
    radius = min(ecc.values())
    return ComponentStats(
        diameter=diameter,
        radius=radius,
        center=frozenset(v for v, e in ecc.items() if e == radius),
        peripheral=frozenset(v for v, e in ecc.items() if e == diameter),
    )


def exact_diameter(adj):
    """Exact diameter / radius / center via one BFS per node.

    O(n * (n + m)) time, O(n + m) extra space.
    """
    adj = normalize_adj(adj)
    stats = tuple(_component_exact(adj, c) for c in connected_components(adj))
    stats = tuple(sorted(stats, key=lambda s: s.diameter, reverse=True))
    if len(stats) > 1:
        return GraphStats(False, math.inf, math.inf, frozenset(), True, stats)
    s = stats[0]
    return GraphStats(True, s.diameter, s.radius, s.center, True, stats)


def _double_sweep(adj, comp, start):
    """One double sweep from ``start``.

    Returns (diameter_lo, radius_ub, centers, peripheral_pair).
    """
    _, a, _, _ = bfs(adj, start)                 # a: farthest from start
    ecc_a, b, _, parent_a = bfs(adj, a)          # (a, b): candidate far pair
    path = _path_to(parent_a, b)
    mids = {path[(len(path) - 1) // 2], path[len(path) // 2]}
    radius_ub = math.inf
    centers = set()
    diameter_lo = ecc_a
    for c in mids:
        ecc_c, _, _, _ = bfs(adj, c)
        diameter_lo = max(diameter_lo, ecc_c)
        if ecc_c < radius_ub:
            radius_ub, centers = ecc_c, {c}
        elif ecc_c == radius_ub:
            centers.add(c)
    return diameter_lo, radius_ub, centers, (a, b)


def estimate_diameter(adj, sweeps=2, rng=None):
    """k-sweep estimate; runs ~4*sweeps BFSs instead of n.

    ``diameter`` is a lower bound on the true diameter, ``radius`` an
    upper bound on the true radius; both are exact on trees.
    """
    adj = normalize_adj(adj)
    comps = list(connected_components(adj))
    if len(comps) > 1:
        stats = tuple(_component_exact(adj, c) for c in comps)
        stats = tuple(sorted(stats, key=lambda s: s.diameter, reverse=True))
        return GraphStats(False, math.inf, math.inf, frozenset(), True, stats)
    comp = sorted(comps[0])
    rng = rng or random.Random(0)

    diameter_lo, radius_ub, centers = 0, math.inf, set()
    for _ in range(max(1, sweeps)):
        start = rng.choice(comp)
        d_lo, r_ub, cs, _ = _double_sweep(adj, comp, start)
        diameter_lo = max(diameter_lo, d_lo)
        if r_ub < radius_ub:
            radius_ub, centers = r_ub, cs
        elif r_ub == radius_ub:
            centers |= cs

    return GraphStats(True, diameter_lo, radius_ub, frozenset(centers),
                      False, (ComponentStats(diameter_lo, radius_ub,
                                             frozenset(centers), frozenset()),))


def assert_relationships(stats, adj=None, exact_check=False):
    """Validate the classic diameter/radius/center relationships.

    For every connected unweighted graph:  rad(G) <= diam(G) <= 2 rad(G),
    and every center c satisfies ecc(c) = rad(G).
    """
    d, r = stats.diameter, stats.radius
    if not stats.connected:
        assert d is math.inf and r is math.inf and not stats.center
        for cs in stats.components:
            _assert_component(cs)
        return stats
    assert isinstance(d, int) and isinstance(r, int)
    assert r <= d <= 2 * r, f"rad/diam inequality violated: {r} {d}"
    assert stats.center, "connected graph must have a center"
    if adj is not None:
        adj = normalize_adj(adj)
        for c in stats.center:
            ecc, _, _, _ = bfs(adj, c)
            assert ecc == r, f"center {c} ecc={ecc} != radius {r}"
    if exact_check:
        assert stats.exact, "expected exact stats"
    return stats


def _assert_component(cs):
    d, r = cs.diameter, cs.radius
    assert r <= d <= 2 * r
    assert cs.center
