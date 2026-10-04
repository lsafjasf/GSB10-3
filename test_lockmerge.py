#!/usr/bin/env python3
"""lockmerge 自测：python3 -m unittest test_lockmerge -v"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

import lockmerge
from lockmerge import DELETED, merge_packages, parse_version, satisfies, validate

HERE = os.path.dirname(os.path.abspath(__file__))


def merge(base, ours, theirs):
    return merge_packages(base, ours, theirs)


class TestParseVersion(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(parse_version("1.2.3"), (1, 2, 3, 0))
        self.assertEqual(parse_version("2"), (2, 0, 0, 0))
        self.assertEqual(parse_version("1.0.0-rc.1"), (1, 0, 0, 0))

    def test_invalid(self):
        for bad in ("", "abc", "1.2.3.4.5", None, "v1.2"):
            with self.assertRaises(ValueError):
                parse_version(bad)


class TestSatisfies(unittest.TestCase):
    def test_exact_and_wildcard(self):
        self.assertTrue(satisfies("1.2.3", "1.2.3"))
        self.assertFalse(satisfies("1.2.4", "1.2.3"))
        self.assertTrue(satisfies("9.9.9", "*"))
        self.assertTrue(satisfies("9.9.9", ""))

    def test_comparators(self):
        self.assertTrue(satisfies("1.5.0", ">=1.0.0, <2.0.0"))
        self.assertFalse(satisfies("2.0.0", ">=1.0.0, <2.0.0"))
        self.assertFalse(satisfies("0.9.0", ">=1.0.0"))
        self.assertTrue(satisfies("1.0.0", "!=1.0.1"))

    def test_caret_and_tilde(self):
        self.assertTrue(satisfies("1.4.0", "^1.2.3"))
        self.assertFalse(satisfies("2.0.0", "^1.2.3"))
        self.assertTrue(satisfies("0.2.9", "^0.2.3"))
        self.assertFalse(satisfies("0.3.0", "^0.2.3"))
        self.assertTrue(satisfies("1.2.9", "~1.2.3"))
        self.assertFalse(satisfies("1.3.0", "~1.2.3"))


class TestThreeWayMerge(unittest.TestCase):
    def test_single_side_change_ours(self):
        merged, conflicts = merge({"a": "1.0.0"}, {"a": "1.1.0"}, {"a": "1.0.0"})
        self.assertEqual(conflicts, [])
        self.assertEqual(merged, {"a": "1.1.0"})

    def test_single_side_change_theirs(self):
        merged, conflicts = merge({"a": "1.0.0"}, {"a": "1.0.0"}, {"a": "2.0.0"})
        self.assertEqual(conflicts, [])
        self.assertEqual(merged, {"a": "2.0.0"})

    def test_both_same_change(self):
        merged, conflicts = merge({"a": "1.0.0"}, {"a": "1.1.0"}, {"a": "1.1.0"})
        self.assertEqual(conflicts, [])
        self.assertEqual(merged, {"a": "1.1.0"})

    def test_both_conflicting_change(self):
        merged, conflicts = merge({"a": "1.0.0"}, {"a": "1.1.0"}, {"a": "1.2.0"})
        self.assertIsNone(merged)
        self.assertEqual(len(conflicts), 1)
        c = conflicts[0]
        self.assertEqual((c.name, c.base, c.ours, c.theirs), ("a", "1.0.0", "1.1.0", "1.2.0"))

    def test_no_change(self):
        merged, conflicts = merge({"a": "1.0.0"}, {"a": "1.0.0"}, {"a": "1.0.0"})
        self.assertEqual((merged, conflicts), ({"a": "1.0.0"}, []))

    # ---- 删除相关 ----

    def test_delete_vs_unchanged(self):
        # 一侧删除、另一侧未动 -> 采用删除
        merged, conflicts = merge({"a": "1.0.0"}, {}, {"a": "1.0.0"})
        self.assertEqual((merged, conflicts), ({}, []))
        merged, conflicts = merge({"a": "1.0.0"}, {"a": "1.0.0"}, {})
        self.assertEqual((merged, conflicts), ({}, []))

    def test_both_delete(self):
        merged, conflicts = merge({"a": "1.0.0"}, {}, {})
        self.assertEqual((merged, conflicts), ({}, []))

    def test_delete_vs_modify_is_conflict(self):
        merged, conflicts = merge({"a": "1.0.0"}, {}, {"a": "1.1.0"})
        self.assertIsNone(merged)
        self.assertEqual(conflicts[0].ours, DELETED)
        self.assertEqual(conflicts[0].theirs, "1.1.0")
        self.assertIn("删除", conflicts[0].reason)

    # ---- 新增相关 ----

    def test_add_one_side(self):
        merged, conflicts = merge({}, {"a": "1.0.0"}, {})
        self.assertEqual((merged, conflicts), ({"a": "1.0.0"}, []))

    def test_add_both_same_version(self):
        merged, conflicts = merge({}, {"a": "1.0.0"}, {"a": "1.0.0"})
        self.assertEqual((merged, conflicts), ({"a": "1.0.0"}, []))

    def test_add_both_different_versions_is_conflict(self):
        merged, conflicts = merge({}, {"a": "1.0.0"}, {"a": "2.0.0"})
        self.assertIsNone(merged)
        self.assertEqual(conflicts[0].base, DELETED)

    def test_mixed_scenario(self):
        base = {"keep": "1.0.0", "up": "1.0.0", "del": "1.0.0", "clash": "1.0.0"}
        ours = {"keep": "1.0.0", "up": "1.1.0", "clash": "2.0.0"}
        theirs = {"keep": "1.0.0", "up": "1.0.0", "clash": "3.0.0", "new": "0.1.0"}
        merged, conflicts = merge(base, ours, theirs)
        self.assertIsNone(merged)
        self.assertEqual([c.name for c in conflicts], ["clash"])


class TestValidate(unittest.TestCase):
    def test_satisfiable(self):
        ok, report = validate({"a": "1.5.0", "b": "2.0.0"}, {"a": ">=1.0.0, <2.0.0"})
        self.assertTrue(ok)
        self.assertTrue(report["satisfiable"])
        self.assertEqual(report["problems"], [])

    def test_unsatisfiable(self):
        ok, report = validate({"a": "2.5.0"}, {"a": "<2.0.0"})
        self.assertFalse(ok)
        self.assertIn("a", report["problems"][0])

    def test_dangling_constraint(self):
        ok, report = validate({}, {"ghost": ">=1.0.0"})
        self.assertFalse(ok)
        self.assertIn("ghost", report["problems"][0])


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, name, data):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path

    def _run(self, *argv):
        return subprocess.run(
            [sys.executable, os.path.join(HERE, "lockmerge.py"), *argv],
            capture_output=True, text=True,
        )

    def test_clean_merge_exit_0(self):
        base = self._write("base.json", {"packages": {"a": "1.0.0"}, "constraints": {"a": ">=1.0.0, <2.0.0"}})
        ours = self._write("ours.json", {"packages": {"a": "1.1.0"}, "constraints": {"a": ">=1.0.0, <2.0.0"}})
        theirs = self._write("theirs.json", {"packages": {"a": "1.0.0"}, "constraints": {"a": ">=1.0.0, <2.0.0"}})
        out = os.path.join(self.tmp.name, "merged.json")
        proc = self._run(base, ours, theirs, "-o", out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("可满足性校验通过", proc.stdout)
        with open(out, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["packages"], {"a": "1.1.0"})

    def test_conflict_exit_1_and_report(self):
        base = self._write("base.json", {"packages": {"a": "1.0.0"}})
        ours = self._write("ours.json", {"packages": {"a": "1.1.0"}})
        theirs = self._write("theirs.json", {"packages": {"a": "1.2.0"}})
        proc = self._run(base, ours, theirs, "--ours-name", "feature-x", "--theirs-name", "main")
        self.assertEqual(proc.returncode, 1)
        # 报告必须指出依赖名、两侧版本与来源
        for token in ("a", "1.1.0", "1.2.0", "feature-x", "main"):
            self.assertIn(token, proc.stdout)

    def test_conflict_json_report(self):
        base = self._write("base.json", {"packages": {"a": "1.0.0"}})
        ours = self._write("ours.json", {"packages": {"a": "1.1.0"}})
        theirs = self._write("theirs.json", {"packages": {}})
        proc = self._run(base, ours, theirs, "--json")
        self.assertEqual(proc.returncode, 1)
        payload = json.loads(proc.stdout)
        c = payload["conflicts"][0]
        self.assertEqual(c["dependency"], "a")
        self.assertEqual(c["theirs"]["version"], "（已删除）")

    def test_unsatisfiable_exit_3(self):
        # 约束交集不可满足：ours 要 <2.0.0，theirs 要 >=2.0.0
        base = self._write("base.json", {"packages": {"a": "1.0.0"}, "constraints": {"a": ">=1.0.0"}})
        ours = self._write("ours.json", {"packages": {"a": "1.5.0"}, "constraints": {"a": ">=1.0.0, <2.0.0"}})
        theirs = self._write("theirs.json", {"packages": {"a": "1.0.0"}, "constraints": {"a": ">=2.0.0"}})
        proc = self._run(base, ours, theirs)
        self.assertEqual(proc.returncode, 3)
        self.assertIn("可满足性校验失败", proc.stderr)

    def test_bad_input_exit_2(self):
        proc = self._run("/nonexistent.json", "/nonexistent.json", "/nonexistent.json")
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
