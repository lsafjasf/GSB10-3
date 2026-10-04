"""Self-tests for lockmerge: semver, three-way merge, satisfiability.

Run: python3 -m unittest -v test_lockmerge
"""

import unittest

import lockmerge
import semver


def dep(version, requires=None):
    entry = {"version": version}
    if requires:
        entry["requires"] = requires
    return entry


def lockfile(deps):
    return {"name": "app", "dependencies": deps}


class SemverTests(unittest.TestCase):
    def test_basic_ordering(self):
        self.assertLess(semver.Version.parse("1.2.3"),
                        semver.Version.parse("1.2.4"))
        self.assertLess(semver.Version.parse("1.2.10"),
                        semver.Version.parse("1.3.0"))

    def test_prerelease_ordering(self):
        self.assertLess(semver.Version.parse("1.0.0-alpha"),
                        semver.Version.parse("1.0.0"))
        self.assertLess(semver.Version.parse("1.0.0-alpha.1"),
                        semver.Version.parse("1.0.0-alpha.2"))

    def test_caret(self):
        self.assertTrue(semver.satisfies("1.5.0", "^1.2.3"))
        self.assertFalse(semver.satisfies("2.0.0", "^1.2.3"))
        self.assertTrue(semver.satisfies("0.2.9", "^0.2.3"))
        self.assertFalse(semver.satisfies("0.3.0", "^0.2.3"))
        self.assertTrue(semver.satisfies("0.0.3", "^0.0.3"))
        self.assertFalse(semver.satisfies("0.0.4", "^0.0.3"))

    def test_tilde(self):
        self.assertTrue(semver.satisfies("1.2.9", "~1.2.3"))
        self.assertFalse(semver.satisfies("1.3.0", "~1.2.3"))

    def test_ranges(self):
        self.assertTrue(semver.satisfies("2.0.0", ">=1.0.0 <3.0.0"))
        self.assertFalse(semver.satisfies("3.0.0", ">=1.0.0 <3.0.0"))
        self.assertTrue(semver.satisfies("4.0.0", "^1.0.0 || ^4.0.0"))
        self.assertFalse(semver.satisfies("2.0.0", "^1.0.0 || ^4.0.0"))
        self.assertTrue(semver.satisfies("1.2.3", "1.2.3"))
        self.assertFalse(semver.satisfies("1.2.4", "1.2.3"))
        self.assertTrue(semver.satisfies("9.9.9", "*"))


class MergeTests(unittest.TestCase):
    BASE = lockfile({
        "lodash": dep("4.17.20"),
        "react": dep("17.0.1"),
        "left-pad": dep("1.3.0"),
        "shared": dep("1.0.0"),
    })

    def test_untouched(self):
        merged, conflicts = lockmerge.merge_lockfiles(
            self.BASE, self.BASE, self.BASE)
        self.assertEqual(conflicts, [])
        self.assertEqual(merged["dependencies"], self.BASE["dependencies"])

    def test_single_side_change_ours(self):
        ours = lockfile({**self.BASE["dependencies"],
                         "lodash": dep("4.17.21")})
        merged, conflicts = lockmerge.merge_lockfiles(
            self.BASE, ours, self.BASE)
        self.assertEqual(conflicts, [])
        self.assertEqual(merged["dependencies"]["lodash"]["version"], "4.17.21")

    def test_single_side_change_theirs(self):
        theirs = lockfile({**self.BASE["dependencies"],
                           "react": dep("17.0.2")})
        merged, conflicts = lockmerge.merge_lockfiles(
            self.BASE, self.BASE, theirs)
        self.assertEqual(conflicts, [])
        self.assertEqual(merged["dependencies"]["react"]["version"], "17.0.2")

    def test_single_side_addition(self):
        ours = lockfile({**self.BASE["dependencies"],
                         "newlib": dep("2.0.0")})
        merged, conflicts = lockmerge.merge_lockfiles(
            self.BASE, ours, self.BASE)
        self.assertEqual(conflicts, [])
        self.assertEqual(merged["dependencies"]["newlib"]["version"], "2.0.0")

    def test_both_same_change(self):
        ours = lockfile({**self.BASE["dependencies"],
                         "lodash": dep("4.17.21")})
        theirs = lockfile({**self.BASE["dependencies"],
                           "lodash": dep("4.17.21")})
        merged, conflicts = lockmerge.merge_lockfiles(
            self.BASE, ours, theirs)
        self.assertEqual(conflicts, [])
        self.assertEqual(merged["dependencies"]["lodash"]["version"], "4.17.21")

    def test_both_conflict(self):
        ours = lockfile({**self.BASE["dependencies"],
                         "lodash": dep("4.17.21")})
        theirs = lockfile({**self.BASE["dependencies"],
                           "lodash": dep("4.18.0")})
        merged, conflicts = lockmerge.merge_lockfiles(
            self.BASE, ours, theirs, our_label="alice", their_label="bob")
        self.assertIsNone(merged)
        self.assertEqual(len(conflicts), 1)
        c = conflicts[0]
        self.assertEqual(c.name, "lodash")
        self.assertEqual(c.kind, "both-modified")
        self.assertEqual(c.base["version"], "4.17.20")
        self.assertEqual(c.ours["version"], "4.17.21")
        self.assertEqual(c.theirs["version"], "4.18.0")
        d = c.to_dict()
        self.assertEqual(d["versions"]["alice"], "4.17.21")
        self.assertEqual(d["versions"]["bob"], "4.18.0")
        self.assertIn("alice", c.render_text())
        self.assertIn("bob", c.render_text())

    def test_both_added_differently(self):
        ours = lockfile({**self.BASE["dependencies"],
                         "newlib": dep("1.0.0")})
        theirs = lockfile({**self.BASE["dependencies"],
                           "newlib": dep("2.0.0")})
        merged, conflicts = lockmerge.merge_lockfiles(
            self.BASE, ours, theirs)
        self.assertIsNone(merged)
        self.assertEqual(conflicts[0].kind, "both-added")
        self.assertIsNone(conflicts[0].base)

    def test_both_added_identically(self):
        ours = lockfile({**self.BASE["dependencies"],
                         "newlib": dep("1.0.0")})
        merged, conflicts = lockmerge.merge_lockfiles(
            self.BASE, ours, ours)
        self.assertEqual(conflicts, [])
        self.assertIn("newlib", merged["dependencies"])

    def test_deleted_on_both_sides(self):
        ours = lockfile({k: v for k, v in self.BASE["dependencies"].items()
                         if k != "left-pad"})
        merged, conflicts = lockmerge.merge_lockfiles(
            self.BASE, ours, ours)
        self.assertEqual(conflicts, [])
        self.assertNotIn("left-pad", merged["dependencies"])

    def test_deleted_one_side_only(self):
        ours = lockfile({k: v for k, v in self.BASE["dependencies"].items()
                         if k != "left-pad"})
        merged, conflicts = lockmerge.merge_lockfiles(
            self.BASE, ours, self.BASE)
        self.assertEqual(conflicts, [])
        self.assertNotIn("left-pad", merged["dependencies"])

    def test_delete_vs_modify_conflict(self):
        ours = lockfile({k: v for k, v in self.BASE["dependencies"].items()
                         if k != "left-pad"})
        theirs = lockfile({**self.BASE["dependencies"],
                           "left-pad": dep("1.4.0")})
        merged, conflicts = lockmerge.merge_lockfiles(
            self.BASE, ours, theirs,
            our_label="alice", their_label="bob")
        self.assertIsNone(merged)
        self.assertEqual(len(conflicts), 1)
        c = conflicts[0]
        self.assertEqual(c.name, "left-pad")
        self.assertEqual(c.kind, "delete-vs-modify")
        self.assertIsNone(c.ours)
        self.assertEqual(c.theirs["version"], "1.4.0")
        self.assertEqual(c.to_dict()["versions"]["alice"], None)

    def test_multiple_conflicts_all_reported(self):
        ours = lockfile({
            "lodash": dep("4.17.21"),
            "react": dep("18.0.0"),
        })
        theirs = lockfile({
            "lodash": dep("4.18.0"),
            "react": dep("17.0.2"),
        })
        base = lockfile({
            "lodash": dep("4.17.20"),
            "react": dep("17.0.1"),
        })
        _, conflicts = lockmerge.merge_lockfiles(base, ours, theirs)
        self.assertEqual({c.name for c in conflicts}, {"lodash", "react"})

    def test_partial_lockfile_match_is_not_conflict(self):
        # Same semantic change (also touched metadata) must count as equal.
        ours = lockfile({**self.BASE["dependencies"],
                         "lodash": dep("4.17.21")})
        theirs = lockfile({**self.BASE["dependencies"],
                           "lodash": dep("4.17.21")})
        merged, conflicts = lockmerge.merge_lockfiles(
            self.BASE, ours, theirs)
        self.assertEqual(conflicts, [])
        self.assertEqual(merged["dependencies"]["lodash"]["version"], "4.17.21")


class ValidationTests(unittest.TestCase):
    def test_satisfiable(self):
        lf = lockfile({
            "app": dep("1.0.0", {"lib-a": "^1.2.0"}),
            "lib-a": dep("1.3.0"),
        })
        self.assertEqual(lockmerge.validate_satisfiability(lf), [])

    def test_unsatisfiable_version(self):
        lf = lockfile({
            "app": dep("1.0.0", {"lib-a": "^1.2.0"}),
            "lib-a": dep("2.0.0"),
        })
        problems = lockmerge.validate_satisfiability(lf)
        self.assertEqual(len(problems), 1)
        self.assertIn("lib-a", problems[0])
        self.assertIn("2.0.0", problems[0])

    def test_missing_dependency(self):
        lf = lockfile({
            "app": dep("1.0.0", {"ghost": "^1.0.0"}),
        })
        problems = lockmerge.validate_satisfiability(lf)
        self.assertEqual(len(problems), 1)
        self.assertIn("not present", problems[0])

    def test_merge_then_validate_passes(self):
        base = lockfile({"lib-a": dep("1.2.0"),
                         "app": dep("1.0.0", {"lib-a": "^1.2.0"})})
        ours = lockfile({"lib-a": dep("1.5.0"),
                         "app": dep("1.0.0", {"lib-a": "^1.2.0"})})
        merged, conflicts = lockmerge.merge_lockfiles(base, ours, base)
        self.assertEqual(conflicts, [])
        self.assertEqual(lockmerge.validate_satisfiability(merged), [])

    def test_report_shape(self):
        lf = lockfile({"app": dep("1.0.0", {"x": "^1.0.0"}),
                       "x": dep("2.0.0")})
        report = lockmerge.build_report(
            [], lockmerge.validate_satisfiability(lf))
        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["conflict_count"], 0)
        self.assertFalse(report["validation"]["satisfiable"])
        self.assertEqual(len(report["validation"]["problems"]), 1)


if __name__ == "__main__":
    unittest.main()
