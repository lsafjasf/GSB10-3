"""基准与解集一致性对比：python3 benchmark.py

输出三张表：
  A. 剪枝前后解集一致性（枚举全部解）
  B. 不同约束强度下的节点数与耗时
  C. 分支定界（软约束优化）剪枝前后最优解一致性
"""

import itertools

import timetabling as tt
from backtrack import Solver


def canon(solutions):
    return {tuple(sorted(s.items())) for s in solutions}


def restrict_days(solver, days):
    for var in solver.domains:
        solver.domains[var] = [v for v in solver.domains[var]
                               if v[0] in days]


def make_instance(days=("Mon", "Tue", "Wed"), periods=3,
                  rooms=None, unavailable=None):
    solver, _, _ = tt.build_problem(
        tt.courses_sample(), rooms or tt.ROOMS_SMALL, periods,
        teacher_unavailable=unavailable or {})
    restrict_days(solver, days)
    return solver


def fmt_row(name, stats_p, stats_n, note):
    return (f"| {name} | {stats_p.nodes} | {stats_p.elapsed:.3f} "
            f"| {stats_n.nodes} | {stats_n.elapsed:.3f} "
            f"| {stats_n.nodes / max(stats_p.nodes, 1):.1f}x | {note} |")


def table_a():
    print("\n## A. 剪枝前后解集一致性（solve_all 枚举全部解）\n")
    print("| 实例 | 剪枝节点 | 剪枝耗时(s) | 未剪枝节点 | 未剪枝耗时(s) "
          "| 节点比 | 解数 | 解集一致 |")
    print("|---|---|---|---|---|---|---|---|")
    cases = [
        ("3天x3节x2教室 基准", make_instance()),
        ("3天x2节x2教室 更紧", make_instance(periods=2)),
        ("2天x3节x2教室 最紧", make_instance(days=("Mon", "Tue"))),
    ]
    for name, solver in cases:
        sols_p, st_p = solver.solve_all(use_pruning=True)
        sols_n, st_n = solver.solve_all(use_pruning=False)
        same = "✓" if canon(sols_p) == canon(sols_n) else "✗ 丢解!"
        print(f"| {name} | {st_p.nodes} | {st_p.elapsed:.3f} "
              f"| {st_n.nodes} | {st_n.elapsed:.3f} "
              f"| {st_n.nodes / max(st_p.nodes, 1):.1f}x "
              f"| {len(sols_p)} | {same} |")


def table_b():
    print("\n## B. 约束强度扫描（教师不可用时段占比 → 节点数/耗时/解数）\n")
    print("| 不可用占比 | 剪枝节点 | 剪枝耗时(s) | 未剪枝节点 "
          "| 未剪枝耗时(s) | 解数 |")
    print("|---|---|---|---|---|---|")
    slots = [(d, p) for d in ("Mon", "Tue", "Wed") for p in (1, 2, 3)]
    for frac in (0.0, 0.2, 0.4, 0.6, 1.0):
        k = int(len(slots) * frac)
        off = {"T1": set(slots[:k]), "T2": set(slots[:k])}
        solver = make_instance(unavailable=off)
        sols_p, st_p = solver.solve_all(use_pruning=True)
        sols_n, st_n = solver.solve_all(use_pruning=False)
        assert canon(sols_p) == canon(sols_n)
        tag = "（冲突→无解）" if frac == 1.0 else ""
        print(f"| {frac:.0%}{tag} | {st_p.nodes} | {st_p.elapsed:.3f} "
              f"| {st_n.nodes} | {st_n.elapsed:.3f} | {len(sols_p)} |")


def table_c():
    print("\n## C. 软约束优化：分支定界剪枝前后最优解一致性\n")
    print("| 实例 | 剪枝节点 | 剪枝耗时(s) | 未剪枝节点 | 未剪枝耗时(s) "
          "| 最优代价 | 最优解数 | 最优解集一致 |")
    print("|---|---|---|---|---|---|---|---|")
    cases = [
        ("3天x3节x2教室", make_instance()),
        ("3天x2节x2教室", make_instance(periods=2)),
        ("2天x3节x2教室", make_instance(days=("Mon", "Tue"))),
    ]
    for name, solver in cases:
        best_p, sols_p, st_p = solver.solve_optimal(use_pruning=True)
        best_n, sols_n, st_n = solver.solve_optimal(use_pruning=False)
        same = "✓" if (best_p == best_n
                       and canon(sols_p) == canon(sols_n)) else "✗"
        print(f"| {name} | {st_p.nodes} | {st_p.elapsed:.3f} "
              f"| {st_n.nodes} | {st_n.elapsed:.3f} "
              f"| {best_p} | {len(sols_p)} | {same} |")
        print(f"  - 剪枝明细: 前向检查剪枝 {st_p.pruned_domains} 次, "
              f"定界剪枝 {st_p.pruned_bound} 次")


def main():
    print("# 排课回溯求解基准数据")
    print("（Python 3，标准库；硬件无关，耗时为单次运行墙钟时间）")
    table_a()
    table_b()
    table_c()


if __name__ == "__main__":
    main()
