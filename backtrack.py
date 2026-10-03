"""通用 CSP 回溯求解器（仅标准库）。

显式建模硬约束与软约束：
- 硬约束 HardConstraint：部分赋值一致性检查，违反即回溯；
- 软约束 SoftConstraint：代价函数（>=0），用于分支定界求最优解。

两种安全剪枝（均不丢解）：
0. 节点一致性（node consistency）：一元硬约束在求解前直接
   作用于初始域，被剔除的值本就不可能出现在任何解中；
1. 前向检查（forward checking）：赋值后剔除其余变量域中所有
   与当前部分赋值冲突的值，某变量域变空则整枝剪掉。
   被剔除的值在任何扩展中都不可能满足约束，故不丢解。
2. 分支定界（branch & bound）：软约束代价非负，部分赋值的代价
   下界单调不减；下界超过当前最优即剪枝，最优解集不变。
"""

from __future__ import annotations

import time
from dataclasses import dataclass


class HardConstraint:
    """硬约束：scope 内变量取值必须满足 predicate。

    predicate(values) 接收 scope 中已赋值变量的值组成的元组。
    - partial=False（默认）：仅当 scope 全部赋值后才检查；
    - partial=True：predicate 需能处理任意子集（如 all-different），
      部分赋值即可判定冲突，从而支持前向检查剪枝。
    """

    def __init__(self, scope, predicate, name=None, partial=False):
        self.scope = tuple(scope)
        self.predicate = predicate
        self.name = name or "hard(" + ",".join(self.scope) + ")"
        self.partial = partial

    def consistent(self, assignment):
        values = tuple(assignment[v] for v in self.scope if v in assignment)
        if len(values) < len(self.scope) and not self.partial:
            return True
        return bool(self.predicate(values))


class SoftConstraint:
    """软约束：代价函数 cost(values) >= 0，值越小越好。

    lower_bound(assignment) 返回该约束在部分赋值下的代价下界，
    用于分支定界。默认实现：scope 全部赋值时返回真实代价，否则 0
    （代价非负，故下界可采纳，剪枝不丢最优解）。
    """

    def __init__(self, scope, cost, name=None, lower_bound=None):
        self.scope = tuple(scope)
        self.cost = cost
        self.name = name or "soft(" + ",".join(self.scope) + ")"
        self._lower_bound = lower_bound

    def full_cost(self, assignment):
        return float(self.cost(tuple(assignment[v] for v in self.scope)))

    def lower_bound(self, assignment):
        if self._lower_bound is not None:
            return float(self._lower_bound(assignment))
        if all(v in self.scope and v in assignment for v in self.scope):
            return self.full_cost(assignment)
        return 0.0


@dataclass
class Stats:
    nodes: int = 0            # 尝试赋值的搜索节点总数
    backtracks: int = 0       # 因硬约束冲突回溯的次数
    pruned_domains: int = 0   # 前向检查导致域为空而剪枝的次数
    pruned_bound: int = 0     # 分支定界剪枝次数
    solutions: int = 0
    elapsed: float = 0.0      # 秒

    def __str__(self):
        return (f"nodes={self.nodes} backtracks={self.backtracks} "
                f"pruned_domains={self.pruned_domains} "
                f"pruned_bound={self.pruned_bound} "
                f"solutions={self.solutions} elapsed={self.elapsed:.4f}s")


class Solver:
    """回溯求解器：枚举全部解 / 求软约束代价最小的最优解。"""

    def __init__(self, domains, hard=(), soft=()):
        self.domains = {v: list(d) for v, d in domains.items()}
        self.hard = list(hard)
        self.soft = list(soft)
        # 节点一致性：一元硬约束预先过滤初始域（安全，不丢解）
        for c in self.hard:
            if len(c.scope) == 1:
                var = c.scope[0]
                self.domains[var] = [d for d in self.domains[var]
                                     if c.consistent({var: d})]
        self._hard_by_var = {}
        for c in self.hard:
            for v in c.scope:
                self._hard_by_var.setdefault(v, []).append(c)

    # ---------- 校验工具 ----------
    def validate(self, assignment):
        """完整赋值是否满足全部硬约束。"""
        return all(c.consistent(assignment) for c in self.hard)

    def total_cost(self, assignment):
        """完整赋值的软约束总代价。"""
        return sum(c.full_cost(assignment) for c in self.soft)

    def _consistent_with(self, var, assignment):
        return all(c.consistent(assignment)
                   for c in self._hard_by_var.get(var, ()))

    # ---------- 枚举全部解 ----------
    def solve_all(self, use_pruning=True, limit=None):
        stats = Stats()
        solutions = []
        t0 = time.perf_counter()
        if use_pruning:
            self._fc_all({}, {v: list(d) for v, d in self.domains.items()},
                         solutions, stats, limit)
        else:
            self._plain_all({}, solutions, stats, limit)
        stats.elapsed = time.perf_counter() - t0
        stats.solutions = len(solutions)
        return solutions, stats

    def _plain_all(self, assignment, solutions, stats, limit):
        if limit is not None and len(solutions) >= limit:
            return
        if len(assignment) == len(self.domains):
            solutions.append(dict(assignment))
            return
        var = next(v for v in self.domains if v not in assignment)
        for value in self.domains[var]:
            stats.nodes += 1
            assignment[var] = value
            if self._consistent_with(var, assignment):
                self._plain_all(assignment, solutions, stats, limit)
            else:
                stats.backtracks += 1
            del assignment[var]

    def _fc_all(self, assignment, domains, solutions, stats, limit):
        if limit is not None and len(solutions) >= limit:
            return
        if not domains:
            solutions.append(dict(assignment))
            return
        var = min(domains, key=lambda v: len(domains[v]))  # MRV
        rest = {v: d for v, d in domains.items() if v != var}
        for value in domains[var]:
            stats.nodes += 1
            assignment[var] = value
            if self._consistent_with(var, assignment):
                new_domains, ok = self._forward_check(rest, assignment)
                if ok:
                    self._fc_all(assignment, new_domains,
                                 solutions, stats, limit)
                else:
                    stats.pruned_domains += 1
                    stats.backtracks += 1
            else:
                stats.backtracks += 1
            del assignment[var]

    def _forward_check(self, rest, assignment):
        """剔除 rest 中各变量与当前赋值冲突的值；任一域变空则失败。"""
        new_domains = {}
        for u, dom in rest.items():
            kept = [d for d in dom if self._value_ok(u, d, assignment)]
            if not kept:
                return None, False
            new_domains[u] = kept
        return new_domains, True

    def _value_ok(self, var, value, assignment):
        assignment[var] = value
        ok = self._consistent_with(var, assignment)
        del assignment[var]
        return ok

    # ---------- 分支定界求最优解 ----------
    def solve_optimal(self, use_pruning=True):
        stats = Stats()
        best = [float("inf")]
        solutions = []
        t0 = time.perf_counter()
        if use_pruning:
            self._fc_opt({}, {v: list(d) for v, d in self.domains.items()},
                         best, solutions, stats)
        else:
            self._plain_opt({}, best, solutions, stats)
        stats.elapsed = time.perf_counter() - t0
        stats.solutions = len(solutions)
        return (best[0] if solutions else None), solutions, stats

    def _record_opt(self, assignment, cost, best, solutions):
        if cost < best[0]:
            best[0] = cost
            solutions.clear()
            solutions.append(dict(assignment))
        elif cost == best[0]:
            solutions.append(dict(assignment))

    def _plain_opt(self, assignment, best, solutions, stats):
        if len(assignment) == len(self.domains):
            self._record_opt(assignment, self.total_cost(assignment),
                             best, solutions)
            return
        var = next(v for v in self.domains if v not in assignment)
        for value in self.domains[var]:
            stats.nodes += 1
            assignment[var] = value
            if self._consistent_with(var, assignment):
                self._plain_opt(assignment, best, solutions, stats)
            else:
                stats.backtracks += 1
            del assignment[var]

    def _fc_opt(self, assignment, domains, best, solutions, stats):
        lower = sum(c.lower_bound(assignment) for c in self.soft)
        if lower > best[0]:  # 下界超过最优：剪枝，不丢最优解
            stats.pruned_bound += 1
            return
        if not domains:
            self._record_opt(assignment, self.total_cost(assignment),
                             best, solutions)
            return
        var = min(domains, key=lambda v: len(domains[v]))
        rest = {v: d for v, d in domains.items() if v != var}
        for value in domains[var]:
            stats.nodes += 1
            assignment[var] = value
            if self._consistent_with(var, assignment):
                new_domains, ok = self._forward_check(rest, assignment)
                if ok:
                    self._fc_opt(assignment, new_domains,
                                 best, solutions, stats)
                else:
                    stats.pruned_domains += 1
                    stats.backtracks += 1
            else:
                stats.backtracks += 1
            del assignment[var]
