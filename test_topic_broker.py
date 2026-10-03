"""SubscriptionTree 的单元测试与对拍测试（标准库 unittest）。"""

import random
import unittest

from topic_broker import (
    InvalidFilterError,
    InvalidTopicError,
    SubscriptionNotFoundError,
    SubscriptionTree,
    filter_matches,
    validate_filter,
    validate_topic,
)


class FilterValidationTest(unittest.TestCase):
    def test_valid_filters(self):
        for f in ["a", "a/b/c", "a/+/c", "+/+", "#", "a/#", "+/#", "a//b", "a/"]:
            self.assertEqual("/".join(validate_filter(f)), f)

    def test_multi_wildcard_must_be_last(self):
        for bad in ["#/a", "a/#/b", "#/#"]:
            with self.assertRaises(InvalidFilterError, msg=bad):
                validate_filter(bad)

    def test_multi_wildcard_must_occupy_whole_level(self):
        for bad in ["a/b#", "a/#c", "##"]:
            with self.assertRaises(InvalidFilterError, msg=bad):
                validate_filter(bad)

    def test_single_wildcard_must_occupy_whole_level(self):
        for bad in ["a/b+", "a/+c", "++", "+a/b"]:
            with self.assertRaises(InvalidFilterError, msg=bad):
                validate_filter(bad)

    def test_empty_and_non_str_filter(self):
        with self.assertRaises(InvalidFilterError):
            validate_filter("")
        with self.assertRaises(InvalidFilterError):
            validate_filter(None)
        with self.assertRaises(InvalidFilterError):
            validate_filter(123)


class TopicValidationTest(unittest.TestCase):
    def test_topic_rejects_wildcards(self):
        for bad in ["a/+", "a/#", "+", "#", "a/b+c"]:
            with self.assertRaises(InvalidTopicError, msg=bad):
                validate_topic(bad)

    def test_topic_rejects_empty_and_non_str(self):
        with self.assertRaises(InvalidTopicError):
            validate_topic("")
        with self.assertRaises(InvalidTopicError):
            validate_topic(None)


class FilterMatchesTest(unittest.TestCase):
    """纯函数 filter_matches 的匹配语义。"""

    CASES = [
        # 精确匹配
        ("a/b/c", "a/b/c", True),
        ("a/b/c", "a/b/d", False),
        ("a/b", "a/b/c", False),
        ("a/b/c", "a/b", False),
        ("a/b", "a//b", False),
        ("A/b", "a/b", False),  # 大小写敏感
        # 单层通配
        ("a/+/c", "a/b/c", True),
        ("a/+/c", "a//c", True),   # '+' 匹配空层级
        ("a/+/c", "a/c", False),
        ("a/+/c", "a/b/c/d", False),
        ("+", "a", True),
        ("+", "a/b", False),
        ("+/+", "a/b", True),
        ("+/+", "a", False),
        # 多层通配
        ("#", "a", True),
        ("#", "a/b/c", True),
        ("#", "/", True),
        ("a/#", "a", True),        # '#' 匹配零层
        ("a/#", "a/b", True),
        ("a/#", "a/b/c/d", True),
        ("a/#", "ab", False),
        ("a/#", "b/a", False),
        ("a/+/#", "a/b", True),
        ("a/+/#", "a/b/c/d", True),
        ("a/+/#", "a", False),
        # 空层级
        ("a//b", "a//b", True),
        ("a//b", "a/b", False),
        ("a/", "a", False),
        ("a/", "a/", True),
        # 中文层级
        ("传感器/+/温度", "传感器/客厅/温度", True),
        ("传感器/#", "传感器/客厅/温度", True),
    ]

    def test_cases(self):
        for topic_filter, topic, expected in self.CASES:
            with self.subTest(filter=topic_filter, topic=topic):
                self.assertIs(filter_matches(topic_filter, topic), expected)


class SubscriptionLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tree = SubscriptionTree()

    def test_subscribe_then_match(self):
        result = self.tree.subscribe("alice", "a/b")
        self.assertTrue(result.is_new)
        self.assertEqual(result.count, 1)
        self.assertEqual(self.tree.subscribers_for("a/b"), {"alice"})
        self.assertEqual(self.tree.subscription_count, 1)

    def test_duplicate_subscribe_counts_references(self):
        self.tree.subscribe("alice", "a/b")
        second = self.tree.subscribe("alice", "a/b")
        self.assertFalse(second.is_new)
        self.assertEqual(second.count, 2)
        self.assertEqual(self.tree.subscription_count, 1)  # 仍是一条订阅
        # 退订必须成对：第一次退订后订阅仍然存活
        first_unsub = self.tree.unsubscribe("alice", "a/b")
        self.assertFalse(first_unsub.removed)
        self.assertEqual(first_unsub.count, 1)
        self.assertEqual(self.tree.subscribers_for("a/b"), {"alice"})
        # 第二次退订才真正移除
        second_unsub = self.tree.unsubscribe("alice", "a/b")
        self.assertTrue(second_unsub.removed)
        self.assertEqual(self.tree.subscription_count, 0)
        self.assertEqual(self.tree.subscribers_for("a/b"), set())

    def test_unsubscribe_nonexistent_raises(self):
        with self.assertRaises(SubscriptionNotFoundError):
            self.tree.unsubscribe("ghost", "a/b")
        self.tree.subscribe("alice", "a/b")
        # 过滤器存在但订阅者不同
        with self.assertRaises(SubscriptionNotFoundError):
            self.tree.unsubscribe("bob", "a/b")
        # 订阅者存在但过滤器不同
        with self.assertRaises(SubscriptionNotFoundError):
            self.tree.unsubscribe("alice", "a/c")
        # 重复退订：成对退完后再次退订同样报错
        self.tree.unsubscribe("alice", "a/b")
        with self.assertRaises(SubscriptionNotFoundError):
            self.tree.unsubscribe("alice", "a/b")

    def test_unsubscribe_error_message_is_explicit(self):
        with self.assertRaises(SubscriptionNotFoundError) as ctx:
            self.tree.unsubscribe("ghost", "x/y")
        message = str(ctx.exception)
        self.assertIn("ghost", message)
        self.assertIn("x/y", message)

    def test_unsubscribe_prunes_empty_nodes(self):
        self.tree.subscribe("alice", "a/b/c/d")
        self.tree.unsubscribe("alice", "a/b/c/d")
        self.assertEqual(self.tree._root.children, {})  # Trie 完全回收

    def test_prune_keeps_shared_prefix(self):
        self.tree.subscribe("alice", "a/b")
        self.tree.subscribe("bob", "a/b/c")
        self.tree.unsubscribe("bob", "a/b/c")
        self.assertEqual(self.tree.subscribers_for("a/b"), {"alice"})
        self.assertEqual(self.tree.subscribers_for("a/b/c"), set())

    def test_invalid_subscriber_rejected(self):
        for bad in ["", None, 42]:
            with self.assertRaises(ValueError):
                self.tree.subscribe(bad, "a/b")
            with self.assertRaises(ValueError):
                self.tree.unsubscribe(bad, "a/b")

    def test_invalid_filter_rejected_on_subscribe_and_unsubscribe(self):
        for bad in ["a/#/b", "a/b+", ""]:
            with self.assertRaises(InvalidFilterError):
                self.tree.subscribe("alice", bad)
            with self.assertRaises(InvalidFilterError):
                self.tree.unsubscribe("alice", bad)


class MatchingTest(unittest.TestCase):
    def setUp(self):
        self.tree = SubscriptionTree()
        self.tree.subscribe("exact", "home/living/temp")
        self.tree.subscribe("single", "home/+/temp")
        self.tree.subscribe("multi", "home/#")
        self.tree.subscribe("all", "#")
        self.tree.subscribe("other", "office/#")

    def test_exact_and_wildcards_together(self):
        self.assertEqual(
            self.tree.subscribers_for("home/living/temp"),
            {"exact", "single", "multi", "all"},
        )

    def test_single_level_wildcard(self):
        self.assertEqual(
            self.tree.subscribers_for("home/bedroom/temp"),
            {"single", "multi", "all"},
        )

    def test_multi_level_wildcard_zero_levels(self):
        # home/# 匹配零层：主题 home 也命中
        self.assertEqual(self.tree.subscribers_for("home"), {"multi", "all"})

    def test_no_false_positive_across_prefix(self):
        self.assertEqual(self.tree.subscribers_for("office/living/temp"),
                         {"other", "all"})
        self.assertEqual(self.tree.subscribers_for("hom"), {"all"})

    def test_iter_matches_reports_filter_and_count(self):
        self.tree.subscribe("exact", "home/living/temp")  # 计数变 2
        matches = sorted(self.tree.iter_matches("home/living/temp"))
        self.assertIn(("exact", "home/living/temp", 2), matches)
        self.assertIn(("single", "home/+/temp", 1), matches)
        self.assertIn(("multi", "home/#", 1), matches)
        self.assertIn(("all", "#", 1), matches)
        self.assertNotIn(("other", "office/#", 1), matches)

    def test_publish_to_wildcard_topic_rejected(self):
        with self.assertRaises(InvalidTopicError):
            self.tree.subscribers_for("home/+/temp")
        with self.assertRaises(InvalidTopicError):
            list(self.tree.iter_matches("home/#"))


class CrossCheckTest(unittest.TestCase):
    """随机数据下，Trie 匹配结果必须与逐条 filter_matches 对拍一致。"""

    def test_trie_matches_naive_scan(self):
        rng = random.Random(7)
        vocab = ["a", "b", "c", "home", "temp", ""]

        def random_topic():
            topic = ""
            while topic == "":
                topic = "/".join(
                    rng.choice(vocab) for _ in range(rng.randint(1, 5))
                )
            return topic

        def random_filter():
            result = ""
            while result == "":
                levels = [
                    rng.choice(vocab + ["+"]) for _ in range(rng.randint(1, 5))
                ]
                if rng.random() < 0.3:
                    levels.append("#")
                result = "/".join(levels)
            return result

        tree = SubscriptionTree()
        subscriptions = []  # (subscriber, filter) 按插入顺序
        for i in range(500):
            f = random_filter()
            tree.subscribe(f"sub-{i}", f)
            subscriptions.append((f"sub-{i}", f))

        for _ in range(300):
            topic = random_topic()
            expected = {
                subscriber
                for subscriber, f in subscriptions
                if filter_matches(f, topic)
            }
            self.assertEqual(tree.subscribers_for(topic), expected)


if __name__ == "__main__":
    unittest.main()
