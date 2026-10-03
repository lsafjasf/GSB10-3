"""subtree: 基于 Trie 的主题订阅树（仅标准库）。

匹配规则（与 MQTT 风格一致）：
- 主题（topic）与订阅过滤器（filter）都由 ``/`` 分隔的层级组成，层级可以为空字符串。
- ``+`` 为单层通配符：必须独占一个层级，匹配该层级的任意一个值（包括空层级）。
- ``#`` 为多层通配符：必须独占一个层级且只能出现在过滤器末尾，
  匹配其后的零个或多个层级（因此 ``a/#`` 也能匹配 ``a`` 本身）。
- 发布的主题不允许包含 ``+`` 或 ``#``。
- 订阅与退订成对生效：subscribe 返回是否真正新增，unsubscribe 返回是否真正移除；
  退订不存在的订阅返回 False 而不是静默成功。
"""

from __future__ import annotations

from collections.abc import Hashable, Iterator

SEPARATOR = "/"
SINGLE_WILDCARD = "+"
MULTI_WILDCARD = "#"


class TopicFilterError(ValueError):
    """订阅过滤器非法。"""


class TopicNameError(ValueError):
    """发布主题非法。"""


def split_filter(topic_filter: str) -> tuple[str, ...]:
    """校验并拆分订阅过滤器，返回层级元组。"""
    if not isinstance(topic_filter, str):
        raise TopicFilterError(f"过滤器必须是字符串，得到 {type(topic_filter).__name__}")
    if topic_filter == "":
        raise TopicFilterError("过滤器不能为空字符串")
    levels = tuple(topic_filter.split(SEPARATOR))
    for index, level in enumerate(levels):
        if MULTI_WILDCARD in level:
            if level != MULTI_WILDCARD:
                raise TopicFilterError(
                    f"多层通配符 '#' 必须独占一个层级: {topic_filter!r}"
                )
            if index != len(levels) - 1:
                raise TopicFilterError(
                    f"多层通配符 '#' 只能出现在末尾层级: {topic_filter!r}"
                )
        elif SINGLE_WILDCARD in level and level != SINGLE_WILDCARD:
            raise TopicFilterError(
                f"单层通配符 '+' 必须独占一个层级: {topic_filter!r}"
            )
    return levels


def split_topic(topic: str) -> tuple[str, ...]:
    """校验并拆分发布主题，返回层级元组。"""
    if not isinstance(topic, str):
        raise TopicNameError(f"主题必须是字符串，得到 {type(topic).__name__}")
    if topic == "":
        raise TopicNameError("主题不能为空字符串")
    levels = tuple(topic.split(SEPARATOR))
    for level in levels:
        if MULTI_WILDCARD in level or SINGLE_WILDCARD in level:
            raise TopicNameError(f"发布主题不允许包含通配符: {topic!r}")
    return levels


class _Node:
    __slots__ = ("children", "subscribers")

    def __init__(self) -> None:
        self.children: dict[str, _Node] = {}
        self.subscribers: set[Hashable] = set()


class SubscriptionTree:
    """订阅树：匹配时只沿主题路径下行，不遍历全部订阅。"""

    def __init__(self) -> None:
        self._root = _Node()
        self._subscription_count = 0
        self._node_count = 1

    def __len__(self) -> int:
        """当前生效的 (过滤器, 订阅者) 订阅对数量。"""
        return self._subscription_count

    @property
    def node_count(self) -> int:
        """Trie 节点数，用于观察内存随订阅规模的增长。"""
        return self._node_count

    def subscribe(self, topic_filter: str, subscriber: Hashable) -> bool:
        """新增订阅。重复添加同一 (过滤器, 订阅者) 返回 False，不产生重复投递。"""
        levels = split_filter(topic_filter)
        node = self._root
        for level in levels:
            child = node.children.get(level)
            if child is None:
                child = _Node()
                node.children[level] = child
                self._node_count += 1
            node = child
        if subscriber in node.subscribers:
            return False
        node.subscribers.add(subscriber)
        self._subscription_count += 1
        return True

    def unsubscribe(self, topic_filter: str, subscriber: Hashable) -> bool:
        """退订。订阅不存在时返回 False（明确结论），存在则移除并回收空节点。"""
        levels = split_filter(topic_filter)
        path: list[tuple[_Node, str]] = []
        node = self._root
        for level in levels:
            child = node.children.get(level)
            if child is None:
                return False
            path.append((node, level))
            node = child
        if subscriber not in node.subscribers:
            return False
        node.subscribers.discard(subscriber)
        self._subscription_count -= 1
        # 自底向上回收不再承载任何订阅的空节点。
        for parent, level in reversed(path):
            child = parent.children[level]
            if child.subscribers or child.children:
                break
            del parent.children[level]
            self._node_count -= 1
        return True

    def subscribers_of(self, topic_filter: str) -> frozenset[Hashable]:
        """查询某过滤器当前的订阅者集合（只读）。"""
        node = self._root
        for level in split_filter(topic_filter):
            node = node.children.get(level)
            if node is None:
                return frozenset()
        return frozenset(node.subscribers)

    def match(self, topic: str) -> set[Hashable]:
        """返回匹配该主题的全部订阅者。只访问主题路径及其通配分支。"""
        matched, _ = self.match_with_stats(topic)
        return matched

    def match_with_stats(self, topic: str) -> tuple[set[Hashable], int]:
        """同 match，但额外返回访问过的 Trie 节点数（用于复杂度验证）。"""
        levels = split_topic(topic)
        matched: set[Hashable] = set()
        visited = 0
        # 栈元素: (节点, 已消费的层级数)
        stack: list[tuple[_Node, int]] = [(self._root, 0)]
        while stack:
            node, depth = stack.pop()
            visited += 1
            hash_child = node.children.get(MULTI_WILDCARD)
            if hash_child is not None:
                # '#' 匹配剩余零个或多个层级，直接命中。
                matched.update(hash_child.subscribers)
                visited += 1
            if depth == len(levels):
                matched.update(node.subscribers)
                continue
            level = levels[depth]
            exact_child = node.children.get(level)
            if exact_child is not None:
                stack.append((exact_child, depth + 1))
            plus_child = node.children.get(SINGLE_WILDCARD)
            if plus_child is not None:
                stack.append((plus_child, depth + 1))
        return matched, visited

    def __contains__(self, topic_filter: object) -> bool:
        if not isinstance(topic_filter, str):
            return False
        try:
            levels = split_filter(topic_filter)
        except TopicFilterError:
            return False
        node = self._root
        for level in levels:
            node = node.children.get(level)
            if node is None:
                return False
        return bool(node.subscribers)

    def __iter__(self) -> Iterator[str]:
        """遍历当前所有有过订阅的过滤器（主要用于调试/测试）。"""
        stack: list[tuple[_Node, list[str]]] = [(self._root, [])]
        while stack:
            node, prefix = stack.pop()
            if node.subscribers:
                yield SEPARATOR.join(prefix)
            for level, child in node.children.items():
                stack.append((child, prefix + [level]))
