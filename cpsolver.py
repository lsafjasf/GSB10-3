"""通用 CSP 回溯求解库（仅 Python 标准库）。

显式建模：
- 变量 + 有限域
- 硬约束 HardConstraint：scope 上的谓词，部分赋值可判违反
- 软约束 SoftConstraint：完整赋值上的代价函数 + 部分赋值上的下界估计

剪枝（均不丢解）：
1. 前向检查（广义）：赋值后过滤未赋值变量的域，只删除与当前部分赋值
   直接违反硬约束的值 —— 被删值不可能出现在任何扩展解中，解集不变。
2. MRV 变量排序：只改变搜索顺序，不改变解集。
3. 分支限界（仅 optimize 模式）：代价下界 >= 当前最优时剪枝，
   下界是 admissible 的（软约束代价非负、下界函数不高估），最优解不丢。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

Assignment = Dict[str, Any]


class HardConstraint:
    """硬约束。predicate(values) 接收 scope 中已赋值变量的字典，
    返回 False 表示当前（部分）赋值已不可扩展，必须满足：
    对任意完整赋值 a，若 a 违反约束，则存在某个部分赋值使 predicate 为 False。
    最简单的写法是：scope 未赋全时返回 True，赋全后做精确检查。"""

    def __init__(self, scope: Iterable[str],
                 predicate: Callable[[Assignment], bool],
                 name: str = ""):
        self.scope = tuple(scope)
        self.predicate = predicate
        self.name = name or f"hard{self.scope}"

    def violated(self, assignment: Assignment) -> bool:
        values = {v: assignment[v] for v in self.scope if v in assignment}
        return not self.predicate(values)

    def __repr__(self) -> str:
        return f"HardConstraint({self.name})"


class SoftConstraint:
    """软约束。cost(values) 在 scope 赋全后给出非负代价；
    lower(values) 在部分赋值上给出未来代价下界（默认 0，admissible）。"""

    def __init__(self, scope: Iterable[str],
                 cost: Callable[[Assignment], float],
                 lower: Optional[Callable[[Assignment], float]] = None,
                 name: str = ""):
        self.scope = tuple(scope)
        self.cost = cost
        self.lower = lower or (lambda values: 0.0)
        self.name = name or f"soft{self.scope}"

    def assigned(self, assignment: Assignment) -> bool:
        return all(v in assignment for v in self.scope)

    def cost_of(self, assignment: Assignment) -> float:
        values = {v: assignment[v] for v in self.scope}
        return self.cost(values)

    def lower_of(self, assignment: Assignment) -> float:
        values = {v: assignment[v] for v in self.scope if v in assignment}
        return self.lower(values)

    def __repr__(self) -> str:
        return f"SoftConstraint({self.name})"


class Problem:
    def __init__(self, name: str = ""):
        self.name = name
        self.domains: Dict[str, List[Any]] = {}
        self.hard: List[HardConstraint] = []
        self.soft: List[SoftConstraint] = []

    def add_variable(self, name: str, domain: Iterable[Any]) -> None:
        domain = list(domain)
        if not domain:
            raise ValueError(f"变量 {name} 的域为空")
        self.domains[name] = domain

    def add_hard(self, scope, predicate, name="") -> None:
        self.hard.append(HardConstraint(scope, predicate, name))

    def add_soft(self, scope, cost, lower=None, name="") -> None:
        self.soft.append(SoftConstraint(scope, cost, lower, name))

    def total_cost(self, assignment: Assignment) -> float:
        return sum(sc.cost_of(assignment) for sc in self.soft)


@dataclass
class Stats:
    nodes: int = 0          # 尝试赋值的搜索节点数
    backtracks: int = 0     # 回溯次数
    fc_pruned: int = 0      # 前向检查删除的域值个数
    bb_pruned: int = 0      # 分支限界剪掉的节点数
    solutions: int = 0
    elapsed: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "nodes": self.nodes,
            "backtracks": self.backtracks,
            "fc_pruned": self.fc_pruned,
            "bb_pruned": self.bb_pruned,
            "solutions": self.solutions,
            "elapsed_ms": round(self.elapsed * 1000, 2),
        }


class Solver:
    def __init__(self, problem: Problem,
                 use_forward_checking: bool = True,
                 use_mrv: bool = True):
        self.p = problem
        self.use_fc = use_forward_checking
        self.use_mrv = use_mrv
        # 每个变量涉及的硬约束，用于前向检查时只查相关变量
        self._hard_by_var: Dict[str, List[HardConstraint]] = {
            v: [] for v in problem.domains}
        for hc in problem.hard:
            for v in hc.scope:
                if v in self._hard_by_var:
                    self._hard_by_var[v].append(hc)

    # ---------- 硬约束检查 ----------

    def _consistent(self, assignment: Assignment) -> bool:
        return not any(hc.violated(assignment) for hc in self.p.hard)

    def _forward_check(self, assignment: Assignment,
                       domains: Dict[str, List[Any]],
                       stats: Stats) -> bool:
        """对未赋值变量过滤域。返回 False 表示出现空域（失败）。"""
        for var, dom in domains.items():
            if var in assignment:
                continue
            kept = []
            for value in dom:
                assignment[var] = value
                ok = not any(hc.violated(assignment)
                             for hc in self._hard_by_var[var])
                del assignment[var]
                if ok:
                    kept.append(value)
                else:
                    stats.fc_pruned += 1
            if not kept:
                return False
            domains[var] = kept
        return True

    def _select_var(self, assignment: Assignment,
                    domains: Dict[str, List[Any]]) -> str:
        unassigned = [v for v in self.p.domains if v not in assignment]
        if self.use_mrv:
            return min(unassigned, key=lambda v: len(domains[v]))
        return unassigned[0]

    # ---------- 枚举所有解 ----------

    def enumerate(self, max_solutions: Optional[int] = None
                  ) -> Tuple[List[Assignment], Stats]:
        stats = Stats()
        solutions: List[Assignment] = []
        assignment: Assignment = {}
        domains = {v: list(d) for v, d in self.p.domains.items()}
        start = time.perf_counter()

        def backtrack(domains: Dict[str, List[Any]]) -> bool:
            # 返回 True 表示达到 max_solutions，应提前终止
            if len(assignment) == len(self.p.domains):
                stats.solutions += 1
                solutions.append(dict(assignment))
                return max_solutions is not None and \
                    stats.solutions >= max_solutions
            var = self._select_var(assignment, domains)
            for value in domains[var]:
                stats.nodes += 1
                assignment[var] = value
                if self._consistent(assignment):
                    if self.use_fc:
                        child_domains = {v: list(d) for v, d in domains.items()}
                        if self._forward_check(assignment, child_domains, stats):
                            if backtrack(child_domains):
                                del assignment[var]
                                return True
                        else:
                            stats.backtracks += 1
                    else:
                        if backtrack(domains):
                            del assignment[var]
                            return True
                else:
                    stats.backtracks += 1
                del assignment[var]
            return False

        backtrack(domains)
        stats.elapsed = time.perf_counter() - start
        return solutions, stats

    # ---------- 分支限界求最优解 ----------

    def optimize(self) -> Tuple[Optional[Assignment], Optional[float], Stats]:
        stats = Stats()
        assignment: Assignment = {}
        domains = {v: list(d) for v, d in self.p.domains.items()}
        best = {"sol": None, "cost": float("inf")}
        start = time.perf_counter()

        def lower_bound() -> float:
            total = 0.0
            for sc in self.p.soft:
                if sc.assigned(assignment):
                    total += sc.cost_of(assignment)
                else:
                    total += sc.lower_of(assignment)
            return total

        def backtrack(domains: Dict[str, List[Any]]) -> None:
            if len(assignment) == len(self.p.domains):
                stats.solutions += 1
                cost = self.p.total_cost(assignment)
                if cost < best["cost"]:
                    best["cost"] = cost
                    best["sol"] = dict(assignment)
                return
            var = self._select_var(assignment, domains)
            for value in domains[var]:
                stats.nodes += 1
                assignment[var] = value
                if self._consistent(assignment):
                    bound = lower_bound()
                    if bound < best["cost"]:
                        child = {v: list(d) for v, d in domains.items()}
                        if not self.use_fc or \
                                self._forward_check(assignment, child, stats):
                            backtrack(child)
                        else:
                            stats.backtracks += 1
                    else:
                        stats.bb_pruned += 1
                else:
                    stats.backtracks += 1
                del assignment[var]

        backtrack(domains)
        stats.elapsed = time.perf_counter() - start
        if best["sol"] is None:
            return None, None, stats
        return best["sol"], best["cost"], stats


def canonical(solutions: List[Assignment]) -> str:
    """解集的规范化字符串，用于剪枝前后解集一致性对比。"""
    import hashlib
    items = sorted(
        tuple(sorted(sol.items())) for sol in solutions)
    body = repr(items).encode()
    return f"count={len(solutions)} sha256={hashlib.sha256(body).hexdigest()[:16]}"
