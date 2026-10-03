"""字典序最小的拓扑排序与依赖校验。

边 ``(before, after)`` 表示 ``before`` 必须先于 ``after`` 构建。
只使用 Python 标准库。
"""

from __future__ import annotations

import heapq
from collections.abc import Hashable, Iterable, Iterator
from typing import TypeVar

Node = TypeVar("Node")

__all__ = [
    "CyclicDependencyError",
    "assert_topological_order",
    "lexicographic_toposort",
]


class CyclicDependencyError(ValueError):
    """依赖图存在环时抛出。

    Attributes:
        cycle_nodes: 环上的节点，按环路径中的首次出现顺序保存，不重复。
        cycle_path: 首尾相同的闭合节点路径，可直接看出环上的每条边。
    """

    def __init__(self, cycle_path: Iterable[Node]) -> None:
        path = tuple(cycle_path)
        if len(path) < 2 or path[0] != path[-1]:
            raise ValueError("cycle_path 必须是首尾相同的闭合路径")

        self.cycle_path = path
        self.cycle_nodes = tuple(dict.fromkeys(path[:-1]))
        nodes_text = ", ".join(map(str, self.cycle_nodes))
        path_text = " -> ".join(map(str, self.cycle_path))
        super().__init__(
            "依赖图中存在环，无法拓扑排序；"
            f"环上节点: {nodes_text}；闭合环路径: {path_text}"
        )


def lexicographic_toposort(
    nodes: Iterable[Node],
    edges: Iterable[tuple[Node, Node]],
) -> list[Node]:
    """返回满足全部依赖边且字典序最小的拓扑序。

    Args:
        nodes: 全部节点。节点必须可哈希、互不重复，并且支持全序比较。
        edges: 依赖边集合；``(before, after)`` 表示 ``before`` 先构建。

    Returns:
        字典序最小的拓扑排序结果。

    Raises:
        CyclicDependencyError: 图中存在环；异常中包含环上节点和闭合环路径。
        TypeError: 节点不可哈希或节点之间不能比较。
        ValueError: 节点重复、边格式错误或边引用了未知节点。

    时间复杂度为 ``O((V + E) log V)``，空间复杂度为 ``O(V + E)``。
    """

    node_list, node_set = _normalize_nodes(nodes)
    _ensure_comparable(node_list)

    adjacency: dict[Node, set[Node]] = {node: set() for node in node_list}
    indegree: dict[Node, int] = {node: 0 for node in node_list}

    for before, after in _iter_edges(edges, node_set):
        # 用 set 存邻接点，重复边只增加一次入度。
        if after not in adjacency[before]:
            adjacency[before].add(after)
            indegree[after] += 1

    # 最小堆保证每一步都取出当前可构建节点中字典序最小者。
    ready = [node for node in node_list if indegree[node] == 0]
    heapq.heapify(ready)
    order: list[Node] = []

    while ready:
        node = heapq.heappop(ready)
        order.append(node)
        for after in adjacency[node]:
            indegree[after] -= 1
            if indegree[after] == 0:
                heapq.heappush(ready, after)

    if len(order) == len(node_list):
        return order

    processed = set(order)
    remaining = node_set - processed
    raise CyclicDependencyError(_find_cycle(adjacency, remaining))


def assert_topological_order(
    nodes: Iterable[Node],
    edges: Iterable[tuple[Node, Node]],
    order: Iterable[Node],
) -> None:
    """逐边断言 ``order`` 是 ``nodes`` 和 ``edges`` 的合法拓扑序。

    排序结果必须恰好包含每个节点一次；随后检查每一条依赖边的前置节点
    都排在后续节点之前。校验失败时抛出 ``ValueError`` 或 ``AssertionError``。
    """

    node_list, node_set = _normalize_nodes(nodes)
    order_list = list(order)

    try:
        order_set = set(order_list)
    except TypeError as exc:
        raise TypeError("排序结果中的节点必须可哈希") from exc

    if len(order_list) != len(node_list) or order_set != node_set:
        raise ValueError("排序结果必须恰好包含每个节点一次")

    position = {node: index for index, node in enumerate(order_list)}
    for before, after in _iter_edges(edges, node_set):
        if position[before] >= position[after]:
            raise AssertionError(
                f"依赖边被违反: {before!r} -> {after!r}；"
                f"位置 {position[before]} 不早于 {position[after]}"
            )


def _normalize_nodes(nodes: Iterable[Node]) -> tuple[list[Node], set[Node]]:
    node_list = list(nodes)
    node_set: set[Node] = set()

    for node in node_list:
        if not isinstance(node, Hashable):
            raise TypeError(f"节点必须可哈希: {node!r}")
        if node in node_set:
            raise ValueError(f"节点重复: {node!r}")
        node_set.add(node)

    return node_list, node_set


def _ensure_comparable(nodes: list[Node]) -> None:
    try:
        sorted(nodes)
    except TypeError as exc:
        raise TypeError("所有节点必须支持相互比较，才能定义字典序") from exc


def _iter_edges(
    edges: Iterable[tuple[Node, Node]],
    node_set: set[Node],
) -> Iterator[tuple[Node, Node]]:
    for index, edge in enumerate(edges):
        try:
            before, after = edge
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"第 {index} 条边必须是 (前置节点, 后续节点) 二元组"
            ) from exc

        for endpoint in (before, after):
            if not isinstance(endpoint, Hashable):
                raise TypeError(f"边端点必须可哈希: {endpoint!r}")
            if endpoint not in node_set:
                raise ValueError(f"边引用了未知节点: {endpoint!r}")

        yield before, after


def _find_cycle(
    adjacency: dict[Node, set[Node]],
    remaining: set[Node],
) -> list[Node]:
    """在 Kahn 算法剩下的子图中找一条有向环。

    Kahn 算法未能输出的节点，其来自剩余节点的入度都大于 0；有限有向图
    中每个节点都有来自剩余集合的前驱，因此必然存在有向环。
    """

    unvisited = set(remaining)
    active: set[Node] = set()
    done: set[Node] = set()

    while unvisited:
        # 固定从最小节点开始，让环检测样例也保持稳定。
        start = min(unvisited)
        unvisited.remove(start)
        active.add(start)

        stack = [start]
        iterators: list[Iterator[Node]] = [iter(adjacency[start] & remaining)]

        while stack:
            advanced = False
            for after in iterators[-1]:
                if after in done:
                    continue
                if after in active:
                    cycle_start = stack.index(after)
                    return stack[cycle_start:] + [after]

                unvisited.remove(after)
                active.add(after)
                stack.append(after)
                iterators.append(iter(adjacency[after] & remaining))
                advanced = True
                break

            if not advanced:
                node = stack.pop()
                iterators.pop()
                active.remove(node)
                done.add(node)

    raise AssertionError("Kahn 剩余子图中应当存在环，但没有找到")


def _demo() -> None:
    nodes = ["fetch", "lint", "build", "test", "package", "deploy"]
    edges = [
        ("fetch", "build"),
        ("lint", "build"),
        ("build", "test"),
        ("test", "package"),
        ("package", "deploy"),
    ]
    order = lexicographic_toposort(nodes, edges)
    assert_topological_order(nodes, edges, order)
    print("字典序最小拓扑序:", " -> ".join(order))

    cyclic_nodes = ["a", "b", "c", "d"]
    cyclic_edges = [("a", "b"), ("b", "c"), ("c", "a"), ("c", "d")]
    try:
        lexicographic_toposort(cyclic_nodes, cyclic_edges)
    except CyclicDependencyError as exc:
        print(exc)
        print("环上节点:", ", ".join(exc.cycle_nodes))
        print("闭合环路径:", " -> ".join(exc.cycle_path))


if __name__ == "__main__":
    _demo()
