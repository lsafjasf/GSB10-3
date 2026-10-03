"""自测：python3 test_backtrack.py  （或 python3 -m unittest -v）"""

import itertools
import random
import unittest

from backtrack import HardConstraint, SoftConstraint, Solver
import timetabling as tt


def canon(solutions):
    """解集合规范化（与变量顺序无关）。"""
    return {tuple(sorted(s.items())) for s in solutions}


class TestEnumeration(unittest.TestCase):
    def test_unique_solution(self):
        # x<y 且 x,y in {1,2} -> 唯一解 (1,2)
        solver = Solver(
            {"x": [1, 2], "y": [1, 2]},
            hard=[HardConstraint(("x", "y"),
                                 lambda v: len(v) < 2 or v[0] < v[1],
                                 partial=True)],
        )
        for pruning in (True, False):
            sols, stats = solver.solve_all(use_pruning=pruning)
            self.assertEqual(canon(sols), {(("x", 1), ("y", 2))})
            self.assertGreater(stats.nodes, 0)

    def test_multiple_solutions(self):
        # x+y<=3, x,y in {1,2} -> (1,1),(1,2),(2,1) 共 3 解
        solver = Solver(
            {"x": [1, 2], "y": [1, 2]},
            hard=[HardConstraint(("x", "y"),
                                 lambda v: sum(v) <= 3, partial=True)],
        )
        for pruning in (True, False):
            sols, _ = solver.solve_all(use_pruning=pruning)
            self.assertEqual(len(sols), 3)

    def test_no_solution(self):
        # x in {1}, y in {1}, x != y -> 无解
        solver = Solver(
            {"x": [1], "y": [1]},
            hard=[HardConstraint(("x", "y"),
                                 lambda v: len(set(v)) == len(v),
                                 partial=True)],
        )
        for pruning in (True, False):
            sols, stats = solver.solve_all(use_pruning=pruning)
            self.assertEqual(sols, [])
            self.assertEqual(stats.solutions, 0)

    def test_conflicting_constraints(self):
        # 两条硬约束互相冲突：x==1 与 x!=1
        solver = Solver(
            {"x": [1, 2]},
            hard=[
                HardConstraint(("x",), lambda v: v[0] == 1, partial=True),
                HardConstraint(("x",), lambda v: v[0] != 1, partial=True),
            ],
        )
        for pruning in (True, False):
            sols, stats = solver.solve_all(use_pruning=pruning)
            self.assertEqual(sols, [])
            # 冲突在一元层即暴露，搜索节点数应很小
            self.assertLessEqual(stats.nodes, 2)

    def test_pruning_preserves_solution_set(self):
        # 随机小 CSP：剪枝前后解集必须完全一致
        rng = random.Random(2026)
        for trial in range(30):
            n = rng.randint(2, 5)
            domains = {f"v{i}": list(range(rng.randint(1, 4)))
                       for i in range(n)}
            hard = []
            for _ in range(rng.randint(1, 6)):
                scope = tuple(rng.sample(list(domains),
                                         k=min(n, rng.randint(1, 3))))
                target = rng.randint(0, 4)
                hard.append(HardConstraint(
                    scope, lambda v, t=target: sum(v) <= t, partial=True))
            solver = Solver(domains, hard=hard)
            pruned, _ = solver.solve_all(use_pruning=True)
            plain, _ = solver.solve_all(use_pruning=False)
            self.assertEqual(canon(pruned), canon(plain),
                             f"trial {trial}: 剪枝丢解/多解")
            for s in pruned:
                self.assertTrue(solver.validate(s))

    def test_solution_limit(self):
        solver = Solver({"x": [1, 2, 3]})
        sols, _ = solver.solve_all(limit=2)
        self.assertEqual(len(sols), 2)


class TestOptimization(unittest.TestCase):
    def test_branch_and_bound_matches_brute_force(self):
        # 软约束最优值与最优解集 = itertools 暴力枚举结果
        rng = random.Random(7)
        for trial in range(20):
            n = rng.randint(2, 4)
            domains = {f"v{i}": list(range(rng.randint(1, 3)))
                       for i in range(n)}
            soft = [SoftConstraint((v,), lambda val, w=rng.randint(1, 5):
                                   float(w * val[0]))
                    for v in domains]
            solver = Solver(domains, soft=soft)
            best, sols, _ = solver.solve_optimal(use_pruning=True)
            # 暴力枚举对照
            brute_best, brute_sols = None, set()
            for combo in itertools.product(*domains.values()):
                assign = dict(zip(domains, combo))
                cost = solver.total_cost(assign)
                if brute_best is None or cost < brute_best:
                    brute_best, brute_sols = cost, {tuple(combo)}
                elif cost == brute_best:
                    brute_sols.add(tuple(combo))
            self.assertEqual(best, brute_best, f"trial {trial}")
            got = {tuple(s[v] for v in domains) for s in sols}
            self.assertEqual(got, brute_sols, f"trial {trial}")

    def test_optimal_unsatisfiable(self):
        solver = Solver(
            {"x": [1]},
            hard=[HardConstraint(("x",), lambda v: v[0] != 1, partial=True)],
            soft=[SoftConstraint(("x",), lambda v: 1.0)],
        )
        for pruning in (True, False):
            best, sols, _ = solver.solve_optimal(use_pruning=pruning)
            self.assertIsNone(best)
            self.assertEqual(sols, [])

    def test_pruning_preserves_optimal_set(self):
        # 小规模排课实例：剪枝/不剪枝的最优值与最优解集一致
        courses = [
            {"name": "数学", "teacher": "T1", "group": "G1", "sessions": 2, "size": 30},
            {"name": "英语", "teacher": "T2", "group": "G1", "sessions": 2, "size": 40},
        ]
        solver, _, _ = tt.build_problem(courses, tt.ROOMS_SMALL, periods=2)
        for var in solver.domains:  # 限定 3 天，控制未剪枝搜索规模
            solver.domains[var] = [v for v in solver.domains[var]
                                   if v[0] in ("Mon", "Tue", "Wed")]
        best_p, sols_p, _ = solver.solve_optimal(use_pruning=True)
        best_n, sols_n, _ = solver.solve_optimal(use_pruning=False)
        self.assertEqual(best_p, best_n)
        self.assertEqual(canon(sols_p), canon(sols_n))


class TestTimetabling(unittest.TestCase):
    def test_solvable_and_valid(self):
        # 收紧到 3 天以控制解的数量，验证每个解都满足全部硬约束
        solver, sessions, meta = tt.build_problem(
            tt.courses_sample(), tt.ROOMS_SMALL, periods=3)
        for var in solver.domains:
            solver.domains[var] = [v for v in solver.domains[var]
                                   if v[0] in ("Mon", "Tue", "Wed")]
        sols, stats = solver.solve_all()
        self.assertGreater(len(sols), 0)
        for s in sols:
            self.assertTrue(solver.validate(s))
            self.assertEqual(set(s), set(sessions))

    def test_unique_solution_instance(self):
        # 1 门课 2 课时、限 Mon/Tue、每天 1 节、1 教室：
        # H4 要求不同天 -> 两天互换共 2 解；再限定 #1 只能周一 -> 唯一解
        courses = [{"name": "数学", "teacher": "T1", "group": "G1",
                    "sessions": 2, "size": 10}]
        solver, _, _ = tt.build_problem(courses, {"R1": 10}, periods=1)
        for var in solver.domains:
            solver.domains[var] = [v for v in solver.domains[var]
                                   if v[0] in ("Mon", "Tue")]
        sols, _ = solver.solve_all()
        self.assertEqual(len(sols), 2)  # 两天互换 -> 2 个解
        # 进一步把 数学#1 的域限定为周一 -> 唯一解
        solver2, _, _ = tt.build_problem(courses, {"R1": 10}, periods=1)
        for var in solver2.domains:
            solver2.domains[var] = [v for v in solver2.domains[var]
                                    if v[0] in ("Mon", "Tue")]
        solver2.domains["数学#1"] = [v for v in solver2.domains["数学#1"]
                                     if v[0] == "Mon"]
        sols2, _ = solver2.solve_all()
        self.assertEqual(len(sols2), 1)
        self.assertEqual(sols2[0]["数学#1"][0], "Mon")
        self.assertEqual(sols2[0]["数学#2"][0], "Tue")

    def test_conflicting_teacher_constraints(self):
        # 教师所有时段均不可用 -> 硬约束互相冲突 -> 无解
        all_slots = {(d, p) for d in tt.DAYS for p in (1, 2, 3)}
        solver, _, _ = tt.build_problem(
            tt.courses_sample(), tt.ROOMS_SMALL, periods=3,
            teacher_unavailable={"T1": all_slots})
        sols, stats = solver.solve_all()
        self.assertEqual(sols, [])
        best, opt_sols, _ = solver.solve_optimal()
        self.assertIsNone(best)
        self.assertEqual(opt_sols, [])

    def test_single_room_conflict(self):
        # 单教室 + 每天 1 节 + 6 课时 > 5 天 -> 无解（教室/时段不足）
        solver, _, _ = tt.build_problem(
            tt.courses_sample(), tt.ROOMS_TIGHT, periods=1)
        sols, _ = solver.solve_all()
        self.assertEqual(sols, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
