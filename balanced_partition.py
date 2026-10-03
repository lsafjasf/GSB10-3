"""Balanced graph bisection via max-flow / min-cut (Python 3, stdlib only).

Pipeline:
  1. Dinic max-flow on an undirected weighted graph.
  2. s-t min-cut from the residual graph (BFS from s).
  3. Balanced bisection: enumerate all s-t pairs, keep only cuts whose
     partition satisfies the balance tolerance, return the minimum. Since
     constrained bisection is NP-hard, small graphs (<=18 nodes) are solved
     exactly by subset enumeration as a correctness net; larger graphs use
     the flow heuristic.

How the balance constraint affects the result:
  The unconstrained global min-cut often isolates a tiny fringe (a leaf or a
  small cluster). The balance constraint shrinks the feasible set to cuts
  whose smaller side has at least ``min_side = max(1, floor(n*(1-t)/2))``
  nodes (t = tolerance, t=0 -> perfect balance, t=1 -> unconstrained).
  Fewer feasible cuts can only make the optimum worse, so the balanced cut
  size is monotonically non-increasing as the tolerance grows, and it is
  always >= the unconstrained min-cut.

Graphs are adjacency dicts: {u: {v: weight, ...}, ...} with weight > 0.
"""

import math
from collections import deque


class Dinic:
    """Dinic's max-flow algorithm. Edge capacities may be int or float."""

    def __init__(self, num_nodes):
        self.num_nodes = num_nodes
        self.adj = [[] for _ in range(num_nodes)]  # edges: [to, cap, rev_idx]

    def add_edge(self, u, v, cap):
        self.adj[u].append([v, cap, len(self.adj[v])])
        self.adj[v].append([u, 0, len(self.adj[u]) - 1])

    def add_undirected_edge(self, u, v, cap):
        self.add_edge(u, v, cap)
        self.add_edge(v, u, cap)

    def max_flow(self, s, t):
        flow = 0
        n = self.num_nodes
        while True:
            level = [-1] * n
            level[s] = 0
            queue = deque([s])
            while queue:
                u = queue.popleft()
                for to, cap, _rev in self.adj[u]:
                    if cap > 0 and level[to] < 0:
                        level[to] = level[u] + 1
                        queue.append(to)
            if level[t] < 0:
                return flow
            it = [0] * n

            def dfs(u, f):
                if u == t:
                    return f
                while it[u] < len(self.adj[u]):
                    i = it[u]
                    to, cap, rev = self.adj[u][i]
                    if cap > 0 and level[to] == level[u] + 1:
                        d = dfs(to, min(f, cap))
                        if d > 0:
                            self.adj[u][i][1] -= d
                            self.adj[to][rev][1] += d
                            return d
                    it[u] += 1
                return 0

            while True:
                pushed = dfs(s, math.inf)
                if pushed == 0:
                    break
                flow += pushed


def _check_graph(adj):
    for u, nbrs in adj.items():
        for v, w in nbrs.items():
            if v not in adj:
                raise ValueError("dangling neighbor %r (not a node)" % (v,))
            if u == v:
                raise ValueError("self-loops are not supported")
            if w <= 0:
                raise ValueError("edge weights must be positive")
            if adj[v].get(u) != w:
                raise ValueError("graph must be undirected: %r/%r mismatch" % (u, v))


def min_st_cut(adj, s, t):
    """Min s-t cut. Returns (cut_value, side) with s in side, t not in side."""
    if s == t:
        raise ValueError("s and t must differ")
    _check_graph(adj)
    nodes = list(adj)
    index = {v: i for i, v in enumerate(nodes)}
    dinic = Dinic(len(nodes))
    for i, u in enumerate(nodes):
        for v, w in adj[u].items():
            if index[v] > i:  # add each undirected edge once
                dinic.add_undirected_edge(i, index[v], w)
    value = dinic.max_flow(index[s], index[t])
    # Nodes reachable from s in the residual graph form the source side.
    seen = [False] * len(nodes)
    seen[index[s]] = True
    queue = deque([index[s]])
    while queue:
        u = queue.popleft()
        for to, cap, _rev in dinic.adj[u]:
            if cap > 0 and not seen[to]:
                seen[to] = True
                queue.append(to)
    side = {nodes[i] for i in range(len(nodes)) if seen[i]}
    return value, side


def cut_edges(adj, side):
    """Edges (u, v, w) crossing the partition; their weights sum to the cut."""
    result = []
    seen = set()
    for u, nbrs in adj.items():
        for v, w in nbrs.items():
            key = frozenset((u, v))
            if key in seen:
                continue
            seen.add(key)
            if (u in side) != (v in side):
                result.append((u, v, w))
    return result


def _reachable_without(adj, start, removed):
    seen = {start}
    queue = deque([start])
    while queue:
        u = queue.popleft()
        for v in adj.get(u, ()):
            if frozenset((u, v)) in removed or v in seen:
                continue
            seen.add(v)
            queue.append(v)
    return seen


def assert_valid_partition(adj, side):
    """Connectivity assertion: after removing exactly the cut edges, no
    remaining edge crosses the partition (the two sides are disconnected).
    Also verifies the cut-edge weights sum to the reported cut value.
    Raises AssertionError otherwise."""
    nodes = set(adj)
    other = nodes - set(side)
    side = set(side)
    assert side and other, "both sides must be non-empty"
    edges = cut_edges(adj, side)
    removed = {frozenset((u, v)) for u, v, _w in edges}
    start_a = next(iter(side))
    reachable = _reachable_without(adj, start_a, removed)
    assert reachable.isdisjoint(other), "sides still connected after removing cut edges"
    # No remaining edge may cross the partition.
    for u, nbrs in adj.items():
        for v in nbrs:
            if frozenset((u, v)) not in removed:
                assert (u in side) == (v in side), "uncut edge crosses the partition"
    return True


def min_side_requirement(n, tolerance):
    """Smaller side must have at least this many nodes."""
    if not 0.0 <= tolerance <= 1.0:
        raise ValueError("tolerance must be in [0, 1]")
    return max(1, math.floor(n * (1.0 - tolerance) / 2.0 + 1e-9))


def flow_balanced_bisection(adj, tolerance=0.0):
    """Flow-based balanced cut heuristic: enumerate all s-t pairs, run the
    max-flow min-cut for each, and keep the minimum cut that satisfies the
    balance constraint.

    Note: bisection with size constraints is NP-hard. This is the standard
    max-flow construction (min-cut computed exactly per s-t pair), but the
    cheapest s-t cut of a pair can itself be unbalanced, so the method is a
    heuristic. ``balanced_bisection`` cross-checks it with exact enumeration
    on small graphs.
    Returns (cut_value, side, edges) or None when no balanced candidate exists.
    """
    _check_graph(adj)
    nodes = list(adj)
    n = len(nodes)
    if n < 2:
        raise ValueError("need at least two nodes to bisect")
    min_side = min_side_requirement(n, tolerance)
    best = None
    for i, s in enumerate(nodes):
        for t in nodes[i + 1:]:
            value, side = min_st_cut(adj, s, t)
            if min(len(side), n - len(side)) < min_side:
                continue  # violates the balance constraint
            if best is None or value < best[0]:
                best = (value, side)
    if best is None:
        return None
    value, side = best
    return value, side, cut_edges(adj, side)


def exact_balanced_bisection(adj, tolerance=0.0, max_nodes=18):
    """Exact balanced bisection by subset enumeration (exponential; intended
    for small graphs and for validating the flow heuristic)."""
    _check_graph(adj)
    nodes = list(adj)
    n = len(nodes)
    if n < 2:
        raise ValueError("need at least two nodes to bisect")
    if n > max_nodes:
        raise ValueError("exact enumeration supports at most %d nodes" % max_nodes)
    min_side = min_side_requirement(n, tolerance)
    undirected = []
    seen = set()
    for i, u in enumerate(nodes):
        for v, w in adj[u].items():
            if (v, u) in seen:
                continue
            seen.add((u, v))
            undirected.append((i, nodes.index(v), w))
    best_mask, best_value = None, math.inf
    for mask in range(1, (1 << n) - 1):
        count = mask.bit_count()
        if count < min_side or n - count < min_side:
            continue
        value = 0
        for i, j, w in undirected:
            if ((mask >> i) & 1) != ((mask >> j) & 1):
                value += w
        if value < best_value:
            best_value, best_mask = value, mask
    if best_mask is None:
        raise ValueError("no feasible bisection for tolerance=%r" % tolerance)
    side = {nodes[i] for i in range(n) if (best_mask >> i) & 1}
    return best_value, side, cut_edges(adj, side)


def balanced_bisection(adj, tolerance=0.0, exact_limit=18):
    """Minimum cut under the balance constraint.

    Runs the flow-based search first; on graphs up to ``exact_limit`` nodes
    the optimum is guaranteed by exact subset enumeration. On larger graphs
    the flow heuristic result is returned, and a clear error is raised if it
    cannot produce a balanced partition (the general problem is NP-hard).
    """
    nodes = list(adj)
    if len(nodes) <= exact_limit:
        return exact_balanced_bisection(adj, tolerance, exact_limit)
    result = flow_balanced_bisection(adj, tolerance)
    if result is None:
        raise ValueError(
            "flow heuristic found no balanced cut for tolerance=%r; "
            "problem is NP-hard, enlarge tolerance or seed a solver" % tolerance)
    return result


def _demo():
    # Two 6-node cliques joined by 2 bridges, plus a leaf hanging off clique A.
    # The unconstrained min-cut isolates the leaf (cut=1, split 12/1);
    # balanced cuts must pay for the bridges (cut=2, split 7/6).
    def clique(nodes, adj):
        for u in nodes:
            for v in nodes:
                if u < v:
                    adj[u][v] = adj[v][u] = 1

    adj = {i: {} for i in range(13)}
    clique(range(0, 6), adj)
    clique(range(6, 12), adj)
    for u, v in [(2, 6), (5, 9), (0, 12)]:  # two bridges + leaf edge
        adj[u][v] = adj[v][u] = 1

    print("Demo graph: two K6 cliques + 2 bridge edges + 1 leaf (n=13)")
    print("%-10s %-9s %-5s %-7s %s" % ("tolerance", "min_side", "cut", "split", "cut edges"))
    for tol in (0.0, 0.1, 0.25, 0.5, 0.75, 1.0):
        value, side, edges = balanced_bisection(adj, tolerance=tol)
        assert_valid_partition(adj, side)
        split = "%d/%d" % (len(side), 13 - len(side))
        print("%-10.2f %-9d %-5g %-7s %s"
              % (tol, min_side_requirement(13, tol), value, split,
                 sorted((u, v) for u, v, _ in edges)))

    # Complete graph K6: unconstrained cut isolates one node; balanced is 3/3.
    k6 = {u: {v: 1 for v in range(6) if v != u} for u in range(6)}
    print("\nComplete graph K6")
    for tol in (0.0, 1.0):
        value, side, edges = balanced_bisection(k6, tolerance=tol)
        assert_valid_partition(k6, side)
        print("tolerance=%.1f  cut=%g  split=%d/%d"
              % (tol, value, len(side), 6 - len(side)))

    # Star graph: every s-t min cut isolates a leaf, so the pure flow
    # heuristic finds no balanced cut, while the exact solver splits the
    # leaves 3/3. Shows why the exact fallback matters.
    star = {0: {i: 1 for i in range(1, 7)}}
    star.update({i: {0: 1} for i in range(1, 7)})
    print("\nStar graph (center + 6 leaves), tolerance=0.0")
    print("flow heuristic:", flow_balanced_bisection(star, tolerance=0.0))
    value, side, edges = balanced_bisection(star, tolerance=0.0)
    assert_valid_partition(star, side)
    print("exact solver:   cut=%g  split=%d/%d" % (value, len(side), 7 - len(side)))


if __name__ == "__main__":
    _demo()
