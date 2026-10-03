"""基于 Trie 的主题订阅树与匹配（MQTT 风格）。

匹配规则
--------
- 主题（topic）与过滤器（filter）都由 ``/`` 分隔成若干层级，例如
  ``a/b/c``。层级可以为空字符串（``a//b`` 的中间层级是 ``""``），
  空层级照常参与匹配。
- 精确匹配：过滤器不含通配符时，逐层级与主题完全相等才命中。
- 单层通配符 ``+``：必须独占一个层级，恰好匹配该位置上的任意一个层级
  （包括空层级），例如 ``a/+/c`` 匹配 ``a/b/c`` 与 ``a//c``，
  不匹配 ``a/c`` 或 ``a/b/c/d``。
- 多层通配符 ``#``：必须独占一个层级且只能出现在过滤器末尾，
  匹配其所在层级及其后的任意多个层级（可为零层），例如
  ``a/#`` 匹配 ``a``、``a/b``、``a/b/c``；单独的 ``#`` 匹配任意主题。
- 过滤器合法但主题含通配符视为非法：发布到 ``a/+`` 这类主题会抛
  ``InvalidTopicError``。

订阅生命周期
------------
- 同一 ``(subscriber, filter)`` 可以重复订阅，内部按引用计数；
  退订必须与之成对调用，计数归零后订阅才真正移除。
- 退订不存在的订阅抛 ``SubscriptionNotFoundError``，绝不静默成功。

匹配复杂度
----------
匹配沿 Trie 自根向下走，每层最多展开“精确子节点 + ``+`` 子节点 +
``#`` 子节点”三条边，耗时只与主题层级数 L 有关（最坏 O(2^L)，
典型 O(L)），与订阅总数 N 无关。``benchmark.py`` 给出实测数据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Set, Tuple

SINGLE_LEVEL_WILDCARD = "+"
MULTI_LEVEL_WILDCARD = "#"
LEVEL_SEPARATOR = "/"


class InvalidFilterError(ValueError):
    """订阅过滤器不合法（通配符位置/形态错误、类型错误等）。"""


class InvalidTopicError(ValueError):
    """待匹配的主题不合法（含通配符或类型错误）。"""


class SubscriptionNotFoundError(KeyError):
    """退订一个不存在的订阅。"""


def _split_levels(value: str, kind: str) -> List[str]:
    if not isinstance(value, str):
        raise TypeError(f"{kind} 必须是 str，得到 {type(value).__name__}")
    if value == "":
        raise ValueError(f"{kind} 不能为空字符串")
    return value.split(LEVEL_SEPARATOR)


def validate_filter(topic_filter: str) -> List[str]:
    """校验并拆分过滤器，返回层级列表；不合法时抛 InvalidFilterError。"""
    try:
        levels = _split_levels(topic_filter, "过滤器")
    except (TypeError, ValueError) as exc:
        raise InvalidFilterError(str(exc)) from exc
    for index, level in enumerate(levels):
        if MULTI_LEVEL_WILDCARD in level:
            if level != MULTI_LEVEL_WILDCARD:
                raise InvalidFilterError(
                    f"多层通配符 '#' 必须独占一个层级: {topic_filter!r}"
                )
            if index != len(levels) - 1:
                raise InvalidFilterError(
                    f"多层通配符 '#' 只能出现在过滤器末尾: {topic_filter!r}"
                )
        elif SINGLE_LEVEL_WILDCARD in level and level != SINGLE_LEVEL_WILDCARD:
            raise InvalidFilterError(
                f"单层通配符 '+' 必须独占一个层级: {topic_filter!r}"
            )
    return levels


def validate_topic(topic: str) -> List[str]:
    """校验并拆分主题，返回层级列表；不合法时抛 InvalidTopicError。"""
    try:
        levels = _split_levels(topic, "主题")
    except (TypeError, ValueError) as exc:
        raise InvalidTopicError(str(exc)) from exc
    for level in levels:
        if (
            SINGLE_LEVEL_WILDCARD in level
            or MULTI_LEVEL_WILDCARD in level
        ):
            raise InvalidTopicError(f"主题不允许包含通配符: {topic!r}")
    return levels


def filter_matches(topic_filter: str, topic: str) -> bool:
    """独立的纯函数匹配：校验后逐层级比对，供测试与基准做对照。"""
    filter_levels = validate_filter(topic_filter)
    topic_levels = validate_topic(topic)
    for index, level in enumerate(filter_levels):
        if level == MULTI_LEVEL_WILDCARD:
            return True  # '#' 必在末尾，匹配剩余零层或多层
        if index >= len(topic_levels):
            return False
        if level != SINGLE_LEVEL_WILDCARD and level != topic_levels[index]:
            return False
    return len(topic_levels) == len(filter_levels)


@dataclass
class _Node:
    """Trie 节点。children 的键是层级字符串或通配符。"""

    children: Dict[str, "_Node"] = field(default_factory=dict)
    # subscriber -> 该订阅者在此过滤器上的剩余订阅次数（引用计数）
    subscribers: Dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class SubscribeResult:
    subscriber: str
    topic_filter: str
    count: int  # 本次调用后该 (subscriber, filter) 的订阅计数

    @property
    def is_new(self) -> bool:
        return self.count == 1


@dataclass(frozen=True)
class UnsubscribeResult:
    subscriber: str
    topic_filter: str
    count: int  # 本次调用后剩余计数，0 表示已彻底移除

    @property
    def removed(self) -> bool:
        return self.count == 0


class SubscriptionTree:
    """订阅树：subscribe/unsubscribe 成对生效，匹配不遍历全部订阅。"""

    def __init__(self) -> None:
        self._root = _Node()
        self._subscription_count = 0  # 计数归零后被移除的订阅条数

    # ---- 生命周期 ----------------------------------------------------

    def subscribe(self, subscriber: str, topic_filter: str) -> SubscribeResult:
        if not isinstance(subscriber, str) or subscriber == "":
            raise ValueError("subscriber 必须是非空 str")
        levels = validate_filter(topic_filter)
        node = self._root
        for level in levels:
            node = node.children.setdefault(level, _Node())
        count = node.subscribers.get(subscriber, 0) + 1
        if count == 1:
            self._subscription_count += 1
        node.subscribers[subscriber] = count
        return SubscribeResult(subscriber, topic_filter, count)

    def unsubscribe(self, subscriber: str, topic_filter: str) -> UnsubscribeResult:
        if not isinstance(subscriber, str) or subscriber == "":
            raise ValueError("subscriber 必须是非空 str")
        levels = validate_filter(topic_filter)
        node = self._root
        path: List[Tuple[_Node, str]] = []
        for level in levels:
            child = node.children.get(level)
            if child is None:
                raise SubscriptionNotFoundError(
                    f"订阅不存在: subscriber={subscriber!r} filter={topic_filter!r}"
                )
            path.append((node, level))
            node = child
        count = node.subscribers.get(subscriber, 0)
        if count == 0:
            raise SubscriptionNotFoundError(
                f"订阅不存在: subscriber={subscriber!r} filter={topic_filter!r}"
            )
        count -= 1
        if count > 0:
            node.subscribers[subscriber] = count
        else:
            del node.subscribers[subscriber]
            self._subscription_count -= 1
            # 自底向上回收不再承载任何订阅的空节点，保持 Trie 紧凑。
            for parent, level in reversed(path):
                child = parent.children[level]
                if child.children or child.subscribers:
                    break
                del parent.children[level]
        return UnsubscribeResult(subscriber, topic_filter, count)

    # ---- 查询 --------------------------------------------------------

    @property
    def subscription_count(self) -> int:
        """当前存活的 (subscriber, filter) 订阅条数。"""
        return self._subscription_count

    def subscribers_for(self, topic: str) -> Set[str]:
        """返回所有能接收该主题的订阅者（去重后的集合）。"""
        topic_levels = validate_topic(topic)
        matched: Set[str] = set()
        self._collect(self._root, topic_levels, 0, matched)
        return matched

    def iter_matches(self, topic: str) -> Iterator[Tuple[str, str, int]]:
        """逐条产出命中的订阅：(subscriber, filter, 剩余计数)。

        与 subscribers_for 的区别是保留过滤器与计数信息，且不去重。
        """
        topic_levels = validate_topic(topic)
        yield from self._walk(self._root, topic_levels, 0, [])

    def _collect(
        self, node: _Node, levels: List[str], index: int, out: Set[str]
    ) -> None:
        multi = node.children.get(MULTI_LEVEL_WILDCARD)
        if multi is not None:
            out.update(multi.subscribers)
        if index == len(levels):
            out.update(node.subscribers)
            return
        exact = node.children.get(levels[index])
        if exact is not None:
            self._collect(exact, levels, index + 1, out)
        single = node.children.get(SINGLE_LEVEL_WILDCARD)
        if single is not None:
            self._collect(single, levels, index + 1, out)

    def _walk(
        self, node: _Node, levels: List[str], index: int, prefix: List[str]
    ) -> Iterator[Tuple[str, str, int]]:
        multi = node.children.get(MULTI_LEVEL_WILDCARD)
        if multi is not None:
            matched_filter = LEVEL_SEPARATOR.join(
                prefix + [MULTI_LEVEL_WILDCARD]
            )
            for subscriber, count in multi.subscribers.items():
                yield subscriber, matched_filter, count
        if index == len(levels):
            if node.subscribers:
                matched_filter = LEVEL_SEPARATOR.join(prefix)
                for subscriber, count in node.subscribers.items():
                    yield subscriber, matched_filter, count
            return
        for key in (levels[index], SINGLE_LEVEL_WILDCARD):
            child = node.children.get(key)
            if child is not None:
                prefix.append(key)
                yield from self._walk(child, levels, index + 1, prefix)
                prefix.pop()
