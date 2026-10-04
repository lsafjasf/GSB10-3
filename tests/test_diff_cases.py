"""对拍测试：用 formulas 库重算 data/eval_cases.json，与独立 oracle 的期望值比对。"""

import unittest

from tests.caselib import load_json, run_eval_case


class DiffCasesTest(unittest.TestCase):
    def test_all_generated_cases(self):
        doc = load_json("eval_cases.json")
        n_checked = 0
        for case in doc["cases"]:
            results, cycles = run_eval_case(case)
            self.assertEqual(cycles, [], "对拍用例不应有环: %s" % case["name"])
            for key, (got, want) in results.items():
                self.assertAlmostEqual(
                    got, want, places=9,
                    msg="%s %s: got=%r want=%r" % (case["name"], key, got, want),
                )
                n_checked += 1
        self.assertGreater(n_checked, 500)


if __name__ == "__main__":
    unittest.main()
