"""Self-tests for balanced_partition (stdlib unittest)."""

import unittest

from balanced_partition import (
    Dinic,
    assert_valid_partition,
    balanced_bisection,
    cut_edges,
    exact_balanced_bisection,
    flow_balanced_bisection,
    min_side_requirement,
    min_st_cut,
)


def clique(nodes, weight=1):
    nodes = list(nodes)
    return {u: {v: weight for v in nodes if v != u} for u in nodes}


def merge(*graphs):
    adj = {}
    for g in graphs:
        for u, nbrs in g.items():
            adj.setdefault(u, {}).update(nbrs)
    return adj


def add_edge(adj, u, v, w=1):
    adj.setdefault(u, {}).setdefault(v, 0)
    adj.setdefault(v, {}).setdefault(u, 0)
    adj[u][v] += w
    adj[v][u] += w


class TestMaxFlow(unittest.TestCase):
    def test_clrs_textbook_example(self):
        # CLRS "Introduction to Algorithms" max-flow example, value 23.
        d = Dinic(6)
        for u, v, c in [(0, 1, 16), (0, 2, 13), (1, 3, 12), (2, 1, 4),
                        (2, 4, 14), (3, 2, 9), (3, 5, 20), (4, 3, 7),
                        (4, 5, 4)]:
            d.add_edge(u, v, c)
        self.assertEqual(d.max_flow(0, 5), 23)

    def test_bottleneck(self):
        d = Dinic(4)
        d.add_edge(0, 1, 100)
        d.add_edge(1, 2, 1)   # bottleneck
        d.add_edge(2, 3, 100)
        d.add_edge(0, 3, 5)
        self.assertEqual(d.max_flow(0, 3), 6)


class TestMinSTCut(unittest.TestCase):
    def test_cut_value_and_side(self):
        adj = {0: {1: 3, 2: 2}, 1: {0: 3, 3: 2}, 2: {0: 2, 3: 3}, 3: {1: 2, 2: 3}}
        value, side = min_st_cut(adj, 0, 3)
        self.assertEqual(value, 4)  # edges (0,1)+(0,2) or (1,3)+(2,3)
        self.assertIn(0, side)
        self.assertNotIn(3, side)
        self.assertEqual(sum(w for *_e, w in cut_edges(adj, side)), value)

    def test_disconnected_st_zero_cut(self):
        adj = {0: {1: 5}, 1: {0: 5}, 2: {3: 5}, 3: {2: 5}}
        value, side = min_st_cut(adj, 0, 2)
        self.assertEqual(value, 0)
        self.assertEqual(side, {0, 1})


class TestBalancedBisection(unittest.TestCase):
    def setUp(self):
        # Two K6 cliques + 2 bridges + 1 leaf (n=13).
        self.g = merge(clique(range(0, 6)), clique(range(6, 12)))
        add_edge(self.g, 2, 6)
        add_edge(self.g, 5, 9)
        add_edge(self.g, 0, 12)

    def test_unbalanced_constraint_isolates_leaf(self):
        value, side, edges = balanced_bisection(self.g, tolerance=1.0)
        self.assertEqual(value, 1)
        self.assertEqual(min(len(side), 13 - len(side)), 1)
        self.assertTrue(assert_valid_partition(self.g, side))

    def test_balanced_constraint_forces_bridge_cut(self):
        value, side, edges = balanced_bisection(self.g, tolerance=0.0)
        self.assertEqual(value, 2)
        self.assertEqual(sorted((len(side), 13 - len(side))), [6, 7])
        self.assertTrue(assert_valid_partition(self.g, side))

    def test_cut_size_monotonic_in_tolerance(self):
        sizes = [balanced_bisection(self.g, tolerance=t)[0]
                 for t in (0.0, 0.1, 0.25, 0.5, 0.75, 1.0)]
        self.assertEqual(sizes, [2, 2, 2, 2, 1, 1])
        self.assertEqual(sizes, sorted(sizes, reverse=True))

    def test_flow_matches_exact_on_demo_graph(self):
        for tol in (0.0, 0.25, 0.5, 1.0):
            flow = flow_balanced_bisection(self.g, tolerance=tol)
            exact = exact_balanced_bisection(self.g, tolerance=tol)
            self.assertEqual(flow[0], exact[0])

    def test_cut_edges_sum_matches_value(self):
        for tol in (0.0, 0.5, 1.0):
            value, side, edges = balanced_bisection(self.g, tolerance=tol)
            self.assertEqual(sum(w for *_e, w in edges), value)


class TestEdgeCases(unittest.TestCase):
    def test_single_node_graph_raises(self):
        with self.assertRaises(ValueError):
            balanced_bisection({0: {}})

    def test_empty_graph_raises(self):
        with self.assertRaises(ValueError):
            balanced_bisection({})

    def test_no_cut_edges_needed(self):
        # Two disjoint components: unconstrained cut value is 0.
        adj = merge(clique([0, 1, 2]), clique([3, 4, 5]))
        value, side, edges = balanced_bisection(adj, tolerance=1.0)
        self.assertEqual(value, 0)
        self.assertEqual(edges, [])
        self.assertTrue(assert_valid_partition(adj, side))

    def test_disconnected_balanced_cut_inside_component(self):
        # Components of size 6 and 3; balance forces cutting the big one.
        adj = merge(clique([0, 1, 2, 3, 4, 5]), clique([6, 7, 8]))
        value, side, edges = balanced_bisection(adj, tolerance=0.0)
        self.assertEqual(value, 5)  # 4/5: K3 plus one K6 node costs 5
        self.assertEqual(sorted((len(side), 9 - len(side))), [4, 5])
        self.assertTrue(assert_valid_partition(adj, side))

    def test_complete_graph(self):
        k6 = clique(range(6))
        value, side, _ = balanced_bisection(k6, tolerance=0.0)
        self.assertEqual((value, len(side)), (9, 3))  # 3x3 bisection
        self.assertTrue(assert_valid_partition(k6, side))
        value, side, _ = balanced_bisection(k6, tolerance=1.0)
        self.assertEqual((value, len(side)), (5, 1))  # isolate one node

    def test_star_flow_heuristic_fails_exact_succeeds(self):
        # Every s-t min cut of a star isolates a leaf, so the pure flow
        # heuristic finds no balanced cut; exact enumeration splits 3/4.
        star = {0: {i: 1 for i in range(1, 7)}}
        star.update({i: {0: 1} for i in range(1, 7)})
        self.assertIsNone(flow_balanced_bisection(star, tolerance=0.0))
        value, side, edges = balanced_bisection(star, tolerance=0.0)
        self.assertEqual(value, 3)
        self.assertTrue(assert_valid_partition(star, side))

    def test_two_nodes(self):
        adj = {"a": {"b": 7}, "b": {"a": 7}}
        value, side, edges = balanced_bisection(adj, tolerance=0.0)
        self.assertEqual(value, 7)
        self.assertEqual(len(edges), 1)
        self.assertTrue(assert_valid_partition(adj, side))

    def test_invalid_tolerance_raises(self):
        with self.assertRaises(ValueError):
            balanced_bisection({0: {1: 1}, 1: {0: 1}}, tolerance=1.5)
        with self.assertRaises(ValueError):
            min_side_requirement(10, -0.1)

    def test_exact_limit_raises(self):
        with self.assertRaises(ValueError):
            exact_balanced_bisection(clique(range(19)), tolerance=0.0)

    def test_weighted_graph(self):
        adj = {0: {1: 10, 2: 1}, 1: {0: 10, 3: 10},
               2: {0: 1, 3: 1}, 3: {1: 10, 2: 1}}
        value, side, _ = balanced_bisection(adj, tolerance=0.0)
        self.assertEqual(value, 11)  # cheapest 2/2 cut (1+10)
        self.assertTrue(assert_valid_partition(adj, side))


if __name__ == "__main__":
    unittest.main(verbosity=2)
