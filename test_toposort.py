import itertools
import unittest

from toposort import (
    CyclicDependencyError,
    assert_topological_order,
    lexicographic_toposort,
)


class LexicographicToposortTests(unittest.TestCase):
    def test_chain_dependencies(self):
        nodes = ["deploy", "test", "build", "fetch"]
        edges = [
            ("fetch", "build"),
            ("build", "test"),
            ("test", "deploy"),
        ]

        order = lexicographic_toposort(nodes, edges)

        self.assertEqual(order, ["fetch", "build", "test", "deploy"])
        assert_topological_order(nodes, edges, order)

    def test_single_node(self):
        nodes = ["only"]
        edges = []

        order = lexicographic_toposort(nodes, edges)

        self.assertEqual(order, ["only"])
        assert_topological_order(nodes, edges, order)

    def test_empty_graph(self):
        order = lexicographic_toposort([], [])

        self.assertEqual(order, [])
        assert_topological_order([], [], order)

    def test_many_parallel_candidates(self):
        count = 1000
        jobs = [f"job-{index:04d}" for index in range(count)]
        nodes = ["end", *reversed(jobs), "start"]
        edges = [("start", job) for job in jobs]
        edges.extend((job, "end") for job in jobs)

        order = lexicographic_toposort(nodes, edges)

        self.assertEqual(order, ["start", *sorted(jobs), "end"])
        assert_topological_order(nodes, edges, order)

    def test_heap_picks_smallest_candidate(self):
        # 普通 FIFO 队列在处理完 a 后会先取 z；最小堆会先取 y。
        nodes = ["z", "y", "b", "a"]
        edges = [("a", "z"), ("b", "y")]

        order = lexicographic_toposort(nodes, edges)

        self.assertEqual(order, ["a", "b", "y", "z"])
        assert_topological_order(nodes, edges, order)

    def test_string_prefix_order(self):
        nodes = ["b", "aa", "a"]
        order = lexicographic_toposort(nodes, [])

        self.assertEqual(order, ["a", "aa", "b"])
        assert_topological_order(nodes, [], order)

    def test_duplicate_edges_do_not_change_result(self):
        nodes = ["a", "b"]
        edges = [("a", "b"), ("a", "b")]

        order = lexicographic_toposort(nodes, edges)

        self.assertEqual(order, ["a", "b"])
        assert_topological_order(nodes, edges, order)

    def test_cycle_reports_nodes_on_cycle(self):
        nodes = ["compile", "link", "load", "run", "standalone"]
        edges = [
            ("compile", "link"),
            ("link", "load"),
            ("load", "compile"),
            ("load", "run"),
        ]

        with self.assertRaises(CyclicDependencyError) as context:
            lexicographic_toposort(nodes, edges)

        exc = context.exception
        self.assertEqual(exc.cycle_nodes, ("compile", "link", "load"))
        self.assertNotIn("run", exc.cycle_nodes)
        self.assertEqual(exc.cycle_path[0], exc.cycle_path[-1])

        edge_set = set(edges)
        for before, after in zip(exc.cycle_path, exc.cycle_path[1:]):
            self.assertIn((before, after), edge_set)
        self.assertIn("compile -> link -> load -> compile", str(exc))

    def test_self_loop_reports_single_node_cycle(self):
        with self.assertRaises(CyclicDependencyError) as context:
            lexicographic_toposort(["a"], [("a", "a")])

        exc = context.exception
        self.assertEqual(exc.cycle_nodes, ("a",))
        self.assertEqual(exc.cycle_path, ("a", "a"))

    def test_result_is_bruteforce_lexicographic_minimum(self):
        nodes = ["a", "b", "c", "d", "e", "f"]
        edges = [
            ("a", "d"),
            ("b", "d"),
            ("d", "e"),
            ("c", "f"),
        ]

        valid_orders = []
        for candidate in itertools.permutations(nodes):
            position = {node: index for index, node in enumerate(candidate)}
            if all(position[before] < position[after] for before, after in edges):
                valid_orders.append(candidate)

        order = tuple(lexicographic_toposort(nodes, edges))

        self.assertEqual(order, min(valid_orders))
        assert_topological_order(nodes, edges, order)

    def test_assertion_reports_violated_edge(self):
        with self.assertRaises(AssertionError) as context:
            assert_topological_order(
                ["a", "b"],
                [("a", "b")],
                ["b", "a"],
            )

        self.assertIn("依赖边被违反", str(context.exception))

    def test_invalid_order_must_contain_every_node_once(self):
        with self.assertRaises(ValueError):
            assert_topological_order(["a", "b"], [], ["a"])
        with self.assertRaises(ValueError):
            assert_topological_order(["a", "b"], [], ["a", "a"])

    def test_invalid_graph_inputs(self):
        with self.assertRaises(ValueError):
            lexicographic_toposort(["a", "a"], [])

        with self.assertRaises(ValueError):
            lexicographic_toposort(["a"], [("a", "b")])

        with self.assertRaises(ValueError):
            lexicographic_toposort(["a", "b", "c"], [("a", "b", "c")])


if __name__ == "__main__":
    unittest.main()
