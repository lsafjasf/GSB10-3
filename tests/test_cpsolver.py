"""回溯求解器自测：无解、唯一解、多解、约束冲突、剪枝等价性、分支限界正确性。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpsolver import Problem, Solver, canonical
from problems import SchedulingSpec, ConfigSpec, build_scheduling, build_config
from instances import all_instances


def solve(problem, fc):
    return Solver(problem, use_forward_checking=fc).enumerate()[0]


class EdgeCases(unittest.TestCase):
    def test_no_solution(self):
        """同教师两门课、只有一个时段一间教室 -> 无解。"""
        spec = SchedulingSpec(
            n_slots=1, rooms={"R1": 40},
            teachers={"C1": "T1", "C2": "T1"})
        problem = build_scheduling(spec)
        sols_prune, stats = Solver(problem, True).enumerate()
        sols_plain = solve(problem, False)
        self.assertEqual(sols_prune, [])
        self.assertEqual(sols_plain, [])
        self.assertGreater(stats.nodes, 0)

    def test_unique_solution(self):
        """容量组合强制 a->M1、b->M2，恰好一个解。"""
        spec = ConfigSpec(
            machines={"M1": (1, 2), "M2": (2, 2)},
            services=["a", "b"],
            demands={"a": 1, "b": 2},
            anti_affinity=[])
        problem = build_config(spec)
        sols = solve(problem, True)
        self.assertEqual(len(sols), 1)
        self.assertEqual(sols[0], {"a": "M1", "b": "M2"})
        self.assertEqual(sols, solve(problem, False))

    def test_multiple_solutions(self):
        """两门课、两时段、两教室、不同教师 -> 12 个注入式排法。"""
        spec = SchedulingSpec(
            n_slots=2, rooms={"R1": 40, "R2": 40},
            teachers={"C1": "T1", "C2": "T2"})
        problem = build_scheduling(spec)
        sols = solve(problem, True)
        self.assertEqual(len(sols), 12)
        self.assertEqual(sols, solve(problem, False))

    def test_conflicting_constraints(self):
        """两门课被锁到同一时段同一教室（H2 直接冲突）-> 无解。"""
        spec = SchedulingSpec(
            n_slots=3, rooms={"R1": 40},
            teachers={"C1": "T1", "C2": "T2"},
            locked={"C1": (0, "R1"), "C2": (0, "R1")})
        problem = build_scheduling(spec)
        self.assertEqual(solve(problem, True), [])

    def test_directly_contradictory_constraints(self):
        """同一变量上两条硬约束互相矛盾：x 必须等于 1 且必须不等于 1。"""
        problem = Problem("contradiction")
        problem.add_variable("x", [1, 2, 3])
        problem.add_hard(["x"], lambda v: v["x"] == 1, name="x==1")
        problem.add_hard(["x"], lambda v: v["x"] != 1, name="x!=1")
        sols_fc, stats_fc = Solver(problem, True).enumerate()
        sols_no, stats_no = Solver(problem, False).enumerate()
        self.assertEqual(sols_fc, [])
        self.assertEqual(sols_no, [])
        # 前向检查在赋值第一个值之前就能判死，节点数应不大于朴素回溯
        self.assertLessEqual(stats_fc.nodes, stats_no.nodes)

    def test_empty_domain_variable(self):
        problem = Problem()
        with self.assertRaises(ValueError):
            problem.add_variable("x", [])

    def test_unsat_optimize_returns_none(self):
        spec = SchedulingSpec(
            n_slots=1, rooms={"R1": 40},
            teachers={"C1": "T1", "C2": "T1"})
        best, cost, _ = Solver(build_scheduling(spec)).optimize()
        self.assertIsNone(best)
        self.assertIsNone(cost)


class PruningEquivalence(unittest.TestCase):
    def test_solution_sets_identical_all_instances(self):
        """剪枝前后解集（数量 + 内容哈希）必须完全一致。"""
        for label, problem in all_instances():
            with self.subTest(instance=label):
                s_fc = solve(problem, True)
                s_no = solve(problem, False)
                self.assertEqual(canonical(s_fc), canonical(s_no),
                                 f"{label} 解集不一致")

    def test_branch_and_bound_optimum_matches_bruteforce(self):
        """分支限界最优值/解必须与全枚举后取最小代价一致。"""
        for label, problem in all_instances():
            with self.subTest(instance=label):
                sols = solve(problem, True)
                if not sols:
                    continue
                brute_best = min(problem.total_cost(s) for s in sols)
                best_sol, best_cost, _ = Solver(problem).optimize()
                self.assertAlmostEqual(best_cost, brute_best)
                self.assertIn(best_sol, sols)
                self.assertAlmostEqual(
                    problem.total_cost(best_sol), brute_best)

    def test_forward_checking_reduces_nodes(self):
        """约束越强，剪枝减少的节点越多；且不会增加节点。"""
        for label, problem in all_instances():
            with self.subTest(instance=label):
                n_fc = Solver(problem, True).enumerate()[1].nodes
                n_no = Solver(problem, False).enumerate()[1].nodes
                self.assertLessEqual(n_fc, n_no)


class SoftConstraintSemantics(unittest.TestCase):
    def test_soft_cost_ordering(self):
        """晚时段排课的软代价应严格高于早时段排课。"""
        early = SchedulingSpec(
            n_slots=2, rooms={"R1": 40}, teachers={"C1": "T1"},
            locked={"C1": (0, "R1")})
        late = SchedulingSpec(
            n_slots=2, rooms={"R1": 40}, teachers={"C1": "T1"},
            locked={"C1": (1, "R1")})
        p_early = build_scheduling(early)
        p_late = build_scheduling(late)
        c_early = p_early.total_cost({"C1": (0, "R1")})
        c_late = p_late.total_cost({"C1": (1, "R1")})
        self.assertLess(c_early, c_late)

    def test_compactness_cost(self):
        """同一教师相邻时段代价 0，间隔时段代价为跨度。"""
        spec = SchedulingSpec(
            n_slots=4, rooms={"R1": 40}, teachers={"C1": "T1", "C2": "T1"})
        p = build_scheduling(spec)
        adjacent = p.total_cost({"C1": (0, "R1"), "C2": (1, "R1")})
        spread = p.total_cost({"C1": (0, "R1"), "C2": (3, "R1")})
        self.assertLess(adjacent, spread)
        self.assertEqual(spread - adjacent, 3.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
