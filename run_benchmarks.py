"""运行基准：剪枝 vs 不剪枝，对比节点数、耗时与解集一致性。

用法：python3 run_benchmarks.py
"""
from cpsolver import Solver, canonical
from instances import all_instances


def main():
    rows = []
    print(f"{'实例':<12} {'方式':<10} {'节点数':>10} {'回溯':>8} "
          f"{'FC删值':>8} {'BB剪枝':>8} {'解数':>7} {'耗时ms':>10}   解集指纹")
    for label, problem in all_instances():
        entry = {"label": label}
        fingerprints = {}
        for mode, fc in (("剪枝", True), ("无剪枝", False)):
            solver = Solver(problem, use_forward_checking=fc, use_mrv=True)
            solutions, stats = solver.enumerate()
            fp = canonical(solutions)
            fingerprints[mode] = fp
            print(f"{label:<12} {mode:<10} {stats.nodes:>10} "
                  f"{stats.backtracks:>8} {stats.fc_pruned:>8} "
                  f"{stats.bb_pruned:>8} {stats.solutions:>7} "
                  f"{stats.elapsed*1000:>10.2f}   {fp}")
            entry[mode] = stats.as_dict()
        same = fingerprints["剪枝"] == fingerprints["无剪枝"]
        print(f"{label:<12} 解集一致: {'是 ✅' if same else '否 ❌'}")
        rows.append((label, entry, same))
        print("-" * 92)

    print("\n分支限界（optimize 模式，仅剪枝求解器输出）：")
    for label, problem in all_instances()[:3]:
        solver = Solver(problem, use_forward_checking=True)
        best, cost, stats = solver.optimize()
        print(f"{label:<12} 节点={stats.nodes:<7} BB剪枝={stats.bb_pruned:<6} "
              f"最优代价={cost}  耗时={stats.elapsed*1000:.2f}ms")

    assert all(same for _, _, same in rows), "剪枝前后解集不一致！"
    print("\n全部实例剪枝前后解集完全一致。")


if __name__ == "__main__":
    main()
