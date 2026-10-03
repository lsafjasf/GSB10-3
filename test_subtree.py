"""订阅树的生命周期测试与边界用例（标准库 unittest，无第三方依赖）。"""

import unittest

from subtree import (
    MULTI_WILDCARD,
    SINGLE_WILDCARD,
    SubscriptionTree,
    TopicFilterError,
    TopicNameError,
)


class MatchingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tree = SubscriptionTree()
        self.filters = [
            "a/b/c",
            "a/+/c",
            "a/#",
            "+/b/c",
            "#",
            "x/+/z",
            "a/+/#",
        ]
        for index, topic_filter in enumerate(self.filters):
            self.assertTrue(self.tree.subscribe(topic_filter, f"u{index}"))

    def test_exact_match(self) -> None:
        self.assertEqual(self.tree.match("a/b/c"), {"u0", "u1", "u2", "u3", "u4", "u6"})

    def test_single_wildcard_matches_one_level(self) -> None:
        self.assertEqual(self.tree.match("a/xyz/c"), {"u1", "u2", "u4", "u6"})
        # '+' 必须恰好匹配一层：不能少层，也不能多层。
        self.assertEqual(self.tree.match("a/c"), {"u2", "u4", "u6"})
        self.assertEqual(self.tree.match("a/b/b/c"), {"u2", "u4", "u6"})

    def test_multi_wildcard_matches_remaining_levels(self) -> None:
        self.assertEqual(self.tree.match("x/y/z"), {"u4", "u5"})
        self.assertEqual(self.tree.match("a/b/c/d/e"), {"u2", "u4", "u6"})

    def test_multi_wildcard_matches_parent_level_zero_levels(self) -> None:
        # '#' 匹配零个剩余层级：a/# 能命中 a 本身；但 a/+/# 不行，'+' 必须占一层。
        self.assertEqual(self.tree.match("a"), {"u2", "u4"})

    def test_hash_only_matches_everything(self) -> None:
        self.assertEqual(self.tree.match("anything"), {"u4"})
        self.assertEqual(self.tree.match("any/thing/at/all"), {"u4"})

    def test_plus_matches_empty_level(self) -> None:
        tree = SubscriptionTree()
        tree.subscribe("a/+/c", "s")
        self.assertEqual(tree.match("a//c"), {"s"})
        self.assertEqual(tree.match("a/b/c"), {"s"})

    def test_no_match(self) -> None:
        tree = SubscriptionTree()
        tree.subscribe("a/b", "s")
        self.assertEqual(tree.match("a/c"), set())
        self.assertEqual(tree.match("a/b/c"), set())
        self.assertEqual(tree.match("x/b"), set())

    def test_level_boundaries_are_respected(self) -> None:
        tree = SubscriptionTree()
        tree.subscribe("a/b", "s")
        self.assertEqual(tree.match("a/bx"), set())
        self.assertEqual(tree.match("xa/b"), set())


class LifecycleTest(unittest.TestCase):
    def test_duplicate_subscription_is_noop(self) -> None:
        tree = SubscriptionTree()
        self.assertTrue(tree.subscribe("a/b", "u1"))
        self.assertFalse(tree.subscribe("a/b", "u1"))  # 重复添加
        self.assertEqual(len(tree), 1)
        self.assertEqual(tree.match("a/b"), {"u1"})

    def test_different_subscribers_same_filter_coexist(self) -> None:
        tree = SubscriptionTree()
        self.assertTrue(tree.subscribe("a/+", "u1"))
        self.assertTrue(tree.subscribe("a/+", "u2"))
        self.assertEqual(len(tree), 2)
        self.assertEqual(tree.match("a/b"), {"u1", "u2"})

    def test_unsubscribe_must_be_paired_with_subscribe(self) -> None:
        tree = SubscriptionTree()
        tree.subscribe("a/+/c", "u1")
        tree.subscribe("a/#", "u1")
        self.assertEqual(tree.match("a/b/c"), {"u1"})  # 同一订阅者命中多次只投一次
        self.assertTrue(tree.unsubscribe("a/+/c", "u1"))
        self.assertEqual(tree.match("a/b/c"), {"u1"})  # 另一条订阅仍然生效
        self.assertFalse(tree.unsubscribe("a/+/c", "u1"))  # 已退订：明确 False
        self.assertFalse(tree.unsubscribe("a/x/y", "u9"))  # 从未订阅：明确 False
        self.assertFalse(tree.unsubscribe("a/#", "u9"))  # 过滤器在但订阅者不在
        self.assertTrue(tree.unsubscribe("a/#", "u1"))
        self.assertEqual(tree.match("a/b/c"), set())
        self.assertEqual(len(tree), 0)

    def test_resubscribe_after_unsubscribe(self) -> None:
        tree = SubscriptionTree()
        tree.subscribe("a/b", "u1")
        tree.unsubscribe("a/b", "u1")
        self.assertTrue(tree.subscribe("a/b", "u1"))
        self.assertEqual(tree.match("a/b"), {"u1"})

    def test_unsubscribe_prunes_empty_nodes(self) -> None:
        tree = SubscriptionTree()
        tree.subscribe("a/b/c", "u1")
        before = tree.node_count
        self.assertTrue(tree.unsubscribe("a/b/c", "u1"))
        self.assertEqual(tree.node_count, 1)  # 只剩根节点
        tree.subscribe("a/b/c", "u1")
        tree.subscribe("a/b/d", "u2")
        tree.unsubscribe("a/b/c", "u1")
        self.assertEqual(tree.match("a/b/d"), {"u2"})
        self.assertEqual(tree.node_count, before)  # 共享前缀保留，c 节点被回收

    def test_subscribers_of(self) -> None:
        tree = SubscriptionTree()
        tree.subscribe("a/+", "u1")
        tree.subscribe("a/+", "u2")
        self.assertEqual(tree.subscribers_of("a/+"), frozenset({"u1", "u2"}))
        self.assertEqual(tree.subscribers_of("a/#"), frozenset())

    def test_contains_and_iterate(self) -> None:
        tree = SubscriptionTree()
        tree.subscribe("a/b", "u1")
        self.assertIn("a/b", tree)
        self.assertNotIn("a/c", tree)
        self.assertEqual(set(tree), {"a/b"})


class ValidationTest(unittest.TestCase):
    def test_hash_must_be_last_level(self) -> None:
        tree = SubscriptionTree()
        for bad in ["a/#/b", "#/a", "a/#/#"]:
            with self.subTest(bad=bad):
                with self.assertRaises(TopicFilterError):
                    tree.subscribe(bad, "u1")

    def test_wildcards_must_own_their_level(self) -> None:
        tree = SubscriptionTree()
        for bad in ["a+b", "a/+b", "a/b#", "a/#b"]:
            with self.subTest(bad=bad):
                with self.assertRaises(TopicFilterError):
                    tree.subscribe(bad, "u1")

    def test_empty_filter_rejected(self) -> None:
        with self.assertRaises(TopicFilterError):
            SubscriptionTree().subscribe("", "u1")

    def test_topic_may_be_empty_level_but_not_empty(self) -> None:
        tree = SubscriptionTree()
        with self.assertRaises(TopicNameError):
            tree.match("")
        tree.subscribe("/a", "s")
        self.assertEqual(tree.match("/a"), {"s"})

    def test_wildcards_forbidden_in_publish_topic(self) -> None:
        tree = SubscriptionTree()
        for bad in ["a/+/c", "a/#", "a+b"]:
            with self.subTest(bad=bad):
                with self.assertRaises(TopicNameError):
                    tree.match(bad)

    def test_non_string_inputs(self) -> None:
        tree = SubscriptionTree()
        with self.assertRaises(TopicFilterError):
            tree.subscribe(42, "u1")  # type: ignore[arg-type]
        with self.assertRaises(TopicNameError):
            tree.match(42)  # type: ignore[arg-type]

    def test_unsubscribe_nonexistent_filter_returns_false_not_raise(self) -> None:
        tree = SubscriptionTree()
        tree.subscribe("a/b", "u1")
        # 非法过滤器仍然报错；合法但不存在的过滤器返回 False。
        self.assertFalse(tree.unsubscribe("x/y", "u1"))
        with self.assertRaises(TopicFilterError):
            tree.unsubscribe("#/bad", "u1")


class NonTraversalTest(unittest.TestCase):
    """匹配不得遍历全部订阅：访问节点数不应随无关订阅数线性增长。"""

    def test_visited_nodes_independent_of_total_subscriptions(self) -> None:
        small = SubscriptionTree()
        small.subscribe("a/b/c", "s")
        _, visited_small = small.match_with_stats("a/b/c")

        big = SubscriptionTree()
        big.subscribe("a/b/c", "s")
        # 在与查询无关的其它前缀下灌入大量订阅。
        for i in range(2000):
            big.subscribe(f"noise/{i % 50}/level{i}", f"s{i}")
        _, visited_big = big.match_with_stats("a/b/c")
        self.assertLessEqual(visited_big, visited_small + 2)


if __name__ == "__main__":
    unittest.main()
