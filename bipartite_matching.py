"""Bipartite maximum matching and maximum-weight maximum matching.

Only Python's standard library is used. A matching is represented as a
``left_node -> right_node`` dictionary.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from numbers import Real
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class MatchingRound:
    """One successful augmentation.

    ``path`` alternates between left and right nodes. For weighted matching,
    ``total_weight`` is the weight of the matching after this round.
    """

    round: int
    previous_size: int
    matching_size: int
    path: Tuple[Any, ...]
    total_weight: Optional[Real] = None

    @property
    def delta(self) -> int:
        return self.matching_size - self.previous_size


@dataclass(frozen=True)
class MatchingResult:
    matching: Dict[Any, Any]
    size: int
    rounds: Tuple[MatchingRound, ...]


@dataclass(frozen=True)
class WeightedMatchingResult:
    matching: Dict[Any, Any]
    size: int
    total_weight: Real
    rounds: Tuple[MatchingRound, ...]


class BipartiteGraph:
    """A bipartite graph with optional numeric edge weights.

    Parallel edges are accepted. For unweighted matching they are
    de-duplicated. For weighted matching, the largest weight among parallel
    edges for the same node pair is used, because one pair can contribute at
    most one edge to a matching.
    """

    def __init__(
        self,
        left_nodes: Iterable[Any],
        right_nodes: Iterable[Any],
        edges: Iterable[Sequence[Any]] = (),
    ) -> None:
        self.left_nodes = tuple(left_nodes)
        self.right_nodes = tuple(right_nodes)
        self._left_set = set(self.left_nodes)
        self._right_set = set(self.right_nodes)

        if len(self._left_set) != len(self.left_nodes):
            raise ValueError("left_nodes contains duplicates")
        if len(self._right_set) != len(self.right_nodes):
            raise ValueError("right_nodes contains duplicates")
        if self._left_set & self._right_set:
            overlap = self._left_set & self._right_set
            raise ValueError(f"left and right partitions overlap: {overlap!r}")

        self._adjacency: Dict[Any, List[Any]] = {
            node: [] for node in self.left_nodes
        }
        self._weights: Dict[Tuple[Any, Any], Real] = {}

        for edge in edges:
            if len(edge) == 2:
                left, right = edge
                weight: Real = 1
            elif len(edge) == 3:
                left, right, weight = edge
            else:
                raise ValueError("each edge must be (left, right[, weight])")
            self.add_edge(left, right, weight)

    @property
    def edge_weights(self) -> Mapping[Tuple[Any, Any], Real]:
        return dict(self._weights)

    def add_edge(self, left: Any, right: Any, weight: Real = 1) -> None:
        if left not in self._left_set:
            raise ValueError(f"unknown left node: {left!r}")
        if right not in self._right_set:
            raise ValueError(f"unknown right node: {right!r}")
        if isinstance(weight, bool) or not isinstance(weight, Real):
            raise TypeError("edge weight must be a real number")
        if not math.isfinite(weight):
            raise ValueError("edge weight must be finite")

        key = (left, right)
        if key not in self._weights:
            self._adjacency[left].append(right)
            self._weights[key] = weight
        else:
            self._weights[key] = max(self._weights[key], weight)

    def has_edge(self, left: Any, right: Any) -> bool:
        return (left, right) in self._weights

    def weight(self, left: Any, right: Any) -> Real:
        return self._weights[(left, right)]

    def neighbors(self, left: Any) -> Tuple[Any, ...]:
        return tuple(self._adjacency[left])


def maximum_matching(graph: BipartiteGraph) -> MatchingResult:
    """Return a maximum-cardinality matching using augmenting paths.

    Each successful DFS augmentation is recorded in ``result.rounds``.
    Edge weights, if present, are intentionally ignored by this function.
    """

    _require_graph(graph)
    match_right: Dict[Any, Any] = {}
    match_left: Dict[Any, Any] = {}
    rounds: List[MatchingRound] = []

    for left in graph.left_nodes:
        path = _find_augmenting_path(left, match_right, graph._adjacency)
        if path is None:
            continue

        previous_size = len(match_left)
        for index in range(0, len(path), 2):
            path_left = path[index]
            path_right = path[index + 1]
            match_right[path_right] = path_left
            match_left[path_left] = path_right

        rounds.append(
            MatchingRound(
                round=len(rounds) + 1,
                previous_size=previous_size,
                matching_size=len(match_left),
                path=tuple(path),
            )
        )

    matching = {
        left: match_left[left] for left in graph.left_nodes if left in match_left
    }
    assert_valid_matching(graph, matching)
    return MatchingResult(matching=matching, size=len(matching), rounds=tuple(rounds))


def maximum_weight_matching(graph: BipartiteGraph) -> WeightedMatchingResult:
    """Return a maximum-weight matching among all maximum-size matchings.

    The objective is lexicographic: first maximize the number of matched
    edges, then maximize the total weight. Thus its size always equals the
    unweighted maximum-matching size, while its total weight is optimal for
    that size. This differs from an unrestricted maximum-weight matching,
    which may use fewer edges when some weights are negative.
    """

    _require_graph(graph)
    left_count = len(graph.left_nodes)
    right_count = len(graph.right_nodes)
    source = 0
    left_offset = 1
    right_offset = left_offset + left_count
    sink = right_offset + right_count
    flow = _MinCostMaxFlow(sink + 1)

    for index in range(left_count):
        flow.add_edge(source, left_offset + index, 1, 0)
    for index in range(right_count):
        flow.add_edge(right_offset + index, sink, 1, 0)

    shift = max([0] + list(graph._weights.values()))
    right_index_by_node = {
        right: index for index, right in enumerate(graph.right_nodes)
    }
    for left_index, left in enumerate(graph.left_nodes):
        for right in graph._adjacency[left]:
            right_index = right_index_by_node[right]
            # Every augmenting path adds one net assignment edge. Adding the
            # same shift to each assignment edge preserves weight ordering and
            # makes the initial reduced costs non-negative.
            cost = shift - graph.weight(left, right)
            flow.add_edge(
                left_offset + left_index,
                right_offset + right_index,
                1,
                cost,
            )

    rounds: List[MatchingRound] = []
    matching: Dict[Any, Any] = {}
    total_weight: Real = 0

    while True:
        path_nodes = flow.augment_one(source, sink)
        if path_nodes is None:
            break

        label_path: List[Any] = []
        for node in path_nodes:
            if left_offset <= node < right_offset:
                label_path.append(graph.left_nodes[node - left_offset])
            elif right_offset <= node < sink:
                label_path.append(graph.right_nodes[node - right_offset])

        matching = _extract_flow_matching(graph, flow, left_offset, right_offset)
        total_weight = sum(
            graph.weight(left, right) for left, right in matching.items()
        )
        rounds.append(
            MatchingRound(
                round=len(rounds) + 1,
                previous_size=len(rounds),
                matching_size=len(matching),
                path=tuple(label_path),
                total_weight=total_weight,
            )
        )

    assert_valid_matching(graph, matching)
    return WeightedMatchingResult(
        matching=matching,
        size=len(matching),
        total_weight=total_weight,
        rounds=tuple(rounds),
    )


def assert_valid_matching(graph: BipartiteGraph, matching: Mapping[Any, Any]) -> bool:
    """Assert that ``matching`` is legal for ``graph``.

    A legal matching uses only declared graph edges, contains nodes from the
    correct partitions, and never repeats a node on either side.
    """

    _require_graph(graph)
    assert isinstance(matching, Mapping), "matching must be a mapping"

    used_left = set()
    used_right = set()
    for left, right in matching.items():
        assert left in graph._left_set, f"unknown left node: {left!r}"
        assert right in graph._right_set, f"unknown right node: {right!r}"
        assert graph.has_edge(left, right), f"not a graph edge: {(left, right)!r}"
        assert left not in used_left, f"left node matched twice: {left!r}"
        assert right not in used_right, f"right node matched twice: {right!r}"
        used_left.add(left)
        used_right.add(right)

    assert len(matching) == len(used_left) == len(used_right)
    return True


def _require_graph(graph: BipartiteGraph) -> None:
    if not isinstance(graph, BipartiteGraph):
        raise TypeError("graph must be a BipartiteGraph")


def _find_augmenting_path(
    start: Any,
    match_right: Mapping[Any, Any],
    adjacency: Mapping[Any, Sequence[Any]],
) -> Optional[List[Any]]:
    """Find one augmenting path without recursion."""

    parent_left: Dict[Any, Optional[Tuple[Any, Any]]] = {start: None}
    seen_right = set()
    stack: List[Tuple[Any, Any]] = [(start, iter(adjacency[start]))]

    while stack:
        left, neighbors = stack[-1]
        advanced = False
        for right in neighbors:
            if right in seen_right:
                continue
            seen_right.add(right)
            matched_left = match_right.get(right)
            if matched_left is None:
                path = [right, left]
                parent = parent_left[left]
                while parent is not None:
                    previous_left, via_right = parent
                    path.extend((via_right, previous_left))
                    parent = parent_left[previous_left]
                path.reverse()
                return path

            if matched_left not in parent_left:
                parent_left[matched_left] = (left, right)
                stack.append((matched_left, iter(adjacency[matched_left])))
                advanced = True
                break

        if not advanced:
            stack.pop()

    return None


@dataclass
class _FlowEdge:
    to: int
    rev: int
    capacity: int
    cost: Real
    original_capacity: int


class _MinCostMaxFlow:
    def __init__(self, node_count: int) -> None:
        self.graph: List[List[_FlowEdge]] = [[] for _ in range(node_count)]
        self._potential: List[Real] = [0] * node_count

    def add_edge(self, source: int, target: int, capacity: int, cost: Real) -> None:
        forward = _FlowEdge(target, len(self.graph[target]), capacity, cost, capacity)
        backward = _FlowEdge(source, len(self.graph[source]), 0, -cost, 0)
        self.graph[source].append(forward)
        self.graph[target].append(backward)

    def augment_one(self, source: int, sink: int) -> Optional[List[int]]:
        """Augment one unit along a minimum-cost source-sink path."""

        node_count = len(self.graph)
        distance: List[Real] = [math.inf] * node_count
        previous_node = [-1] * node_count
        previous_edge = [-1] * node_count
        distance[source] = 0
        queue: List[Tuple[Real, int]] = [(0, source)]

        while queue:
            current_distance, node = heapq.heappop(queue)
            if current_distance != distance[node]:
                continue
            for edge_index, edge in enumerate(self.graph[node]):
                if edge.capacity <= 0:
                    continue
                reduced_cost = (
                    edge.cost + self._potential[node] - self._potential[edge.to]
                )
                candidate = current_distance + reduced_cost
                if candidate < distance[edge.to]:
                    distance[edge.to] = candidate
                    previous_node[edge.to] = node
                    previous_edge[edge.to] = edge_index
                    heapq.heappush(queue, (candidate, edge.to))

        if distance[sink] == math.inf:
            return None

        for node in range(node_count):
            if distance[node] < math.inf:
                self._potential[node] += distance[node]

        path = [sink]
        node = sink
        while node != source:
            parent = previous_node[node]
            edge_index = previous_edge[node]
            edge = self.graph[parent][edge_index]
            edge.capacity -= 1
            self.graph[node][edge.rev].capacity += 1
            path.append(parent)
            node = parent
        path.reverse()
        return path


def _extract_flow_matching(
    graph: BipartiteGraph,
    flow: _MinCostMaxFlow,
    left_offset: int,
    right_offset: int,
) -> Dict[Any, Any]:
    matching: Dict[Any, Any] = {}
    for left_index, left in enumerate(graph.left_nodes):
        flow_node = left_offset + left_index
        for edge in flow.graph[flow_node]:
            if not (right_offset <= edge.to < right_offset + len(graph.right_nodes)):
                continue
            if edge.original_capacity == 1 and edge.capacity == 0:
                right = graph.right_nodes[edge.to - right_offset]
                matching[left] = right
                break
    return matching


def _demo() -> None:
    graph = BipartiteGraph(
        ["任务A", "任务B", "任务C", "任务D"],
        ["执行者甲", "执行者乙", "执行者丙"],
        [
            ("任务A", "执行者甲"),
            ("任务A", "执行者乙"),
            ("任务B", "执行者甲"),
            ("任务C", "执行者乙"),
            ("任务C", "执行者丙"),
            ("任务D", "执行者丙"),
        ],
    )
    result = maximum_matching(graph)
    print("最大匹配:", result.matching)
    print("匹配数量:", result.size)
    for item in result.rounds:
        print(
            f"第{item.round}轮: {item.previous_size} -> {item.matching_size}, "
            f"增广路径={item.path}"
        )

    weighted_graph = BipartiteGraph(
        ["任务A", "任务B", "任务C"],
        ["执行者甲", "执行者乙", "执行者丙"],
        [
            ("任务A", "执行者甲", 9),
            ("任务A", "执行者乙", 8),
            ("任务B", "执行者甲", 7),
            ("任务B", "执行者丙", 6),
            ("任务C", "执行者乙", 10),
            ("任务C", "执行者丙", 1),
        ],
    )
    weighted = maximum_weight_matching(weighted_graph)
    print("最大基数下的最大权匹配:", weighted.matching)
    print("匹配数量:", weighted.size, "总权重:", weighted.total_weight)
    for item in weighted.rounds:
        print(
            f"第{item.round}轮: {item.previous_size} -> {item.matching_size}, "
            f"累计权重={item.total_weight}, 增广路径={item.path}"
        )


if __name__ == "__main__":
    _demo()
