import random
import unittest

from bipartite_matching import (
    BipartiteGraph,
    assert_valid_matching,
    maximum_matching,
    maximum_weight_matching,
)


class BipartiteMatchingTests(unittest.TestCase):
    def test_empty_graph(self):
        graph = BipartiteGraph([], [], [])

        result = maximum_matching(graph)
        weighted = maximum_weight_matching(graph)

        self.assertEqual(result.size, 0)
        self.assertEqual(result.matching, {})
        self.assertEqual(result.rounds, ())
        self.assertEqual(weighted.size, 0)
        self.assertEqual(weighted.total_weight, 0)
        self.assertEqual(weighted.rounds, ())
        self.assertTrue(assert_valid_matching(graph, result.matching))
        self.assertTrue(assert_valid_matching(graph, weighted.matching))

    def test_complete_bipartite_graph(self):
        left = ["L1", "L2", "L3", "L4"]
        right = ["R1", "R2", "R3", "R4"]
        graph = BipartiteGraph(
            left,
            right,
            [(l, r, (i + 1) * (j + 2)) for i, l in enumerate(left) for j, r in enumerate(right)],
        )

        result = maximum_matching(graph)
        weighted = maximum_weight_matching(graph)

        self.assertEqual(result.size, 4)
        self.assertEqual([item.matching_size for item in result.rounds], [1, 2, 3, 4])
        self.assertEqual([item.delta for item in result.rounds], [1, 1, 1, 1])
        self.assertEqual(weighted.size, result.size)
        self.assertEqual(weighted.total_weight, 40)
        self.assertTrue(assert_valid_matching(graph, result.matching))
        self.assertTrue(assert_valid_matching(graph, weighted.matching))

    def test_isolated_nodes_on_both_sides(self):
        graph = BipartiteGraph(
            ["task", "isolated_task"],
            ["worker", "isolated_worker"],
            [("task", "worker", 3)],
        )

        result = maximum_matching(graph)
        weighted = maximum_weight_matching(graph)

        self.assertEqual(result.matching, {"task": "worker"})
        self.assertEqual(result.size, 1)
        self.assertEqual(weighted.matching, {"task": "worker"})
        self.assertEqual(weighted.total_weight, 3)
        self.assertTrue(assert_valid_matching(graph, result.matching))
        self.assertTrue(assert_valid_matching(graph, weighted.matching))

    def test_parallel_edges_are_deduplicated_and_use_best_weight(self):
        graph = BipartiteGraph(
            ["A", "B"],
            ["X", "Y"],
            [
                ("A", "X", 1),
                ("A", "X", 10),
                ("A", "X", 4),
                ("B", "Y", 2),
            ],
        )

        result = maximum_matching(graph)
        weighted = maximum_weight_matching(graph)

        self.assertEqual(graph.neighbors("A"), ("X",))
        self.assertEqual(graph.weight("A", "X"), 10)
        self.assertEqual(result.size, 2)
        self.assertEqual(weighted.matching, {"A": "X", "B": "Y"})
        self.assertEqual(weighted.total_weight, 12)
        self.assertTrue(assert_valid_matching(graph, result.matching))
        self.assertTrue(assert_valid_matching(graph, weighted.matching))

    def test_weighted_matching_first_preserves_maximum_cardinality(self):
        graph = BipartiteGraph(
            ["A", "B"],
            ["X", "Y"],
            [
                ("A", "X", 100),
                ("A", "Y", 99),
                ("B", "X", 98),
            ],
        )

        unweighted = maximum_matching(graph)
        weighted = maximum_weight_matching(graph)

        self.assertEqual(unweighted.size, 2)
        self.assertEqual(weighted.size, unweighted.size)
        self.assertEqual(weighted.matching, {"A": "Y", "B": "X"})
        self.assertEqual(weighted.total_weight, 197)
        self.assertTrue(assert_valid_matching(graph, weighted.matching))

    def test_negative_weights_still_use_a_maximum_size_matching(self):
        graph = BipartiteGraph(
            ["A", "B"],
            ["X", "Y"],
            [
                ("A", "X", -5),
                ("A", "Y", 1),
                ("B", "X", -5),
            ],
        )

        weighted = maximum_weight_matching(graph)

        # An unrestricted maximum-weight matching would choose only (A, Y)=1.
        # The required objective first keeps cardinality 2, then maximizes weight.
        self.assertEqual(weighted.size, 2)
        self.assertEqual(weighted.matching, {"A": "Y", "B": "X"})
        self.assertEqual(weighted.total_weight, -4)
        self.assertTrue(assert_valid_matching(graph, weighted.matching))

    def test_validity_assertion_rejects_repeated_or_unknown_nodes(self):
        graph = BipartiteGraph(
            ["A", "B"],
            ["X", "Y"],
            [("A", "X"), ("A", "Y"), ("B", "X"), ("B", "Y")],
        )

        with self.assertRaisesRegex(AssertionError, "right node matched twice"):
            assert_valid_matching(graph, {"A": "X", "B": "X"})
        with self.assertRaisesRegex(AssertionError, "unknown left node"):
            assert_valid_matching(graph, {"C": "X"})
        with self.assertRaisesRegex(AssertionError, "unknown right node"):
            assert_valid_matching(graph, {"A": "Z"})

        invalid_edge_graph = BipartiteGraph(["A", "B"], ["X"], [("A", "X")])
        with self.assertRaisesRegex(AssertionError, "not a graph edge"):
            assert_valid_matching(invalid_edge_graph, {"B": "X"})

    def test_random_small_graphs_against_brute_force(self):
        rng = random.Random(169)

        for _ in range(40):
            left = [f"L{i}" for i in range(rng.randrange(5))]
            right = [f"R{i}" for i in range(rng.randrange(5))]
            edges = []
            for l_node in left:
                for r_node in right:
                    if rng.random() < 0.55:
                        for _ in range(rng.randrange(1, 4)):
                            edges.append((l_node, r_node, rng.randrange(-5, 20)))

            graph = BipartiteGraph(left, right, edges)
            unweighted = maximum_matching(graph)
            weighted = maximum_weight_matching(graph)
            best_size, best_weight = _brute_force_best(graph)

            self.assertEqual(unweighted.size, best_size)
            self.assertEqual(weighted.size, best_size)
            self.assertEqual(weighted.total_weight, best_weight)
            self.assertEqual(
                [item.matching_size for item in unweighted.rounds],
                list(range(1, best_size + 1)),
            )
            self.assertEqual(
                [item.matching_size for item in weighted.rounds],
                list(range(1, best_size + 1)),
            )
            self.assertTrue(assert_valid_matching(graph, unweighted.matching))
            self.assertTrue(assert_valid_matching(graph, weighted.matching))


def _brute_force_best(graph):
    best_size = 0
    best_weight = 0

    def search(left_index, used_right, size, weight):
        nonlocal best_size, best_weight
        if left_index == len(graph.left_nodes):
            if size > best_size:
                best_size = size
                best_weight = weight
            elif size == best_size:
                best_weight = max(best_weight, weight)
            return

        left = graph.left_nodes[left_index]
        search(left_index + 1, used_right, size, weight)
        for right in graph.neighbors(left):
            if right not in used_right:
                search(
                    left_index + 1,
                    used_right | {right},
                    size + 1,
                    weight + graph.weight(left, right),
                )

    search(0, set(), 0, 0)
    return best_size, best_weight


if __name__ == "__main__":
    unittest.main()
