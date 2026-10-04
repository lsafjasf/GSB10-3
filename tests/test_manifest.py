"""Self tests for the manifest tool (standard library unittest only).

Run: python -m unittest discover -s tests -v
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from manifest_tool import (  # noqa: E402
    Manifest,
    ManifestFormatError,
    UnsafePathError,
    generate_manifest,
    verify_manifest,
    verify_manifest_self,
    write_manifest,
)
from manifest_tool.manifest import (  # noqa: E402
    normalize_relpath,
)

MANIFEST_NAME = "MANIFEST.jsonl"


def write(path, content=b""):
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
    with open(path, "wb") as handle:
        handle.write(content if isinstance(content, bytes) else content.encode())


def make_and_verify(root, manifest_name=MANIFEST_NAME):
    manifest_path = os.path.join(root, manifest_name)
    write_manifest(generate_manifest(root, exclude=[manifest_name]), manifest_path)
    return manifest_path, verify_manifest(root, manifest_path)


class PathNormalizationTests(unittest.TestCase):
    def test_collapses_redundant_components(self):
        self.assertEqual(normalize_relpath("a/./b//c"), "a/b/c")
        self.assertEqual(normalize_relpath("a/b/../c"), "a/c")

    def test_rejects_absolute_and_traversal(self):
        for bad in ("/etc/passwd", "../x", "a/../../x", "..\\x", "C:/x"):
            with self.assertRaises(UnsafePathError):
                normalize_relpath(bad)

    def test_rejects_empty(self):
        for bad in ("", ".", "./", "/"):
            with self.assertRaises(UnsafePathError):
                normalize_relpath(bad)


class RoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_unchanged_tree_passes(self):
        write(os.path.join(self.root, "a.txt"), "hello")
        write(os.path.join(self.root, "sub", "b.txt"), "world")
        _, report = make_and_verify(self.root)
        self.assertTrue(report.manifest_ok)
        self.assertTrue(report.ok)
        self.assertEqual(sorted(report.unchanged), ["a.txt", "sub/b.txt"])

    def test_modified_file_detected(self):
        write(os.path.join(self.root, "a.txt"), "v1")
        manifest_path, _ = make_and_verify(self.root)
        write(os.path.join(self.root, "a.txt"), "v2")
        report = verify_manifest(self.root, manifest_path)
        self.assertFalse(report.ok)
        self.assertEqual([p for p, _ in report.modified], ["a.txt"])
        self.assertIn("content changed", report.modified[0][1])

    def test_modified_same_size_detected(self):
        write(os.path.join(self.root, "a.txt"), "abc")
        manifest_path, _ = make_and_verify(self.root)
        write(os.path.join(self.root, "a.txt"), "xyz")
        report = verify_manifest(self.root, manifest_path)
        self.assertEqual([p for p, _ in report.modified], ["a.txt"])
        self.assertIn("same size", report.modified[0][1])

    def test_deleted_file_detected(self):
        write(os.path.join(self.root, "keep.txt"), "x")
        write(os.path.join(self.root, "gone.txt"), "y")
        manifest_path, _ = make_and_verify(self.root)
        os.remove(os.path.join(self.root, "gone.txt"))
        report = verify_manifest(self.root, manifest_path)
        self.assertEqual(report.deleted, ["gone.txt"])

    def test_added_file_detected(self):
        write(os.path.join(self.root, "keep.txt"), "x")
        manifest_path, _ = make_and_verify(self.root)
        write(os.path.join(self.root, "new.txt"), "y")
        report = verify_manifest(self.root, manifest_path)
        self.assertEqual(report.added, ["new.txt"])

    def test_all_three_categories_at_once(self):
        write(os.path.join(self.root, "same.txt"), "1")
        write(os.path.join(self.root, "edit.txt"), "2")
        write(os.path.join(self.root, "drop.txt"), "3")
        manifest_path, _ = make_and_verify(self.root)
        write(os.path.join(self.root, "edit.txt"), "changed")
        os.remove(os.path.join(self.root, "drop.txt"))
        write(os.path.join(self.root, "extra.txt"), "4")
        report = verify_manifest(self.root, manifest_path)
        self.assertEqual([p for p, _ in report.modified], ["edit.txt"])
        self.assertEqual(report.deleted, ["drop.txt"])
        self.assertEqual(report.added, ["extra.txt"])
        self.assertEqual(report.unchanged, ["same.txt"])


class EmptyDirectoryTests(unittest.TestCase):
    def test_empty_directory_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            manifest_path, report = make_and_verify(root)
            self.assertTrue(report.ok)
            self.assertEqual(report.unchanged, [])
            # Adding a file after the fact is still detected.
            write(os.path.join(root, "x.txt"), "x")
            report = verify_manifest(root, manifest_path)
            self.assertEqual(report.added, ["x.txt"])


class CaseSensitivityTests(unittest.TestCase):
    def test_same_name_different_case_are_distinct(self):
        with tempfile.TemporaryDirectory() as root:
            write(os.path.join(root, "File.txt"), "lower")
            write(os.path.join(root, "FILE.txt"), "upper")
            manifest = generate_manifest(root, exclude=[MANIFEST_NAME])
            if len(manifest.entries) == 2:
                manifest_path = os.path.join(root, MANIFEST_NAME)
                write_manifest(manifest, manifest_path)
                report = verify_manifest(root, manifest_path)
                self.assertTrue(report.ok)
                self.assertTrue(
                    any("case-collision: FILE.txt/File.txt" == w
                        for w in report.warnings),
                    report.warnings,
                )
            else:
                self.skipTest("filesystem is case-insensitive")


class SymlinkTests(unittest.TestCase):
    def setUp(self):
        if not hasattr(os, "symlink"):
            self.skipTest("os.symlink unavailable")
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_symlinks_recorded_but_not_followed(self):
        write(os.path.join(self.root, "target.txt"), "data")
        os.symlink("target.txt", os.path.join(self.root, "link.txt"))
        os.mkdir(os.path.join(self.root, "realdir"))
        write(os.path.join(self.root, "realdir", "inside.txt"), "nested")
        os.symlink("realdir", os.path.join(self.root, "dirlink"))
        manifest = generate_manifest(self.root, exclude=[MANIFEST_NAME])
        types = {e.path: e.type for e in manifest.entries}
        self.assertEqual(types["link.txt"], "symlink")
        self.assertEqual(types["dirlink"], "symlink")
        # The target reached through the directory symlink is not double counted.
        self.assertNotIn("dirlink/inside.txt", types)
        self.assertEqual(types["realdir/inside.txt"], "file")

    def test_symlink_target_change_detected(self):
        write(os.path.join(self.root, "a.txt"), "a")
        write(os.path.join(self.root, "b.txt"), "b")
        os.symlink("a.txt", os.path.join(self.root, "link"))
        manifest_path = os.path.join(self.root, MANIFEST_NAME)
        write_manifest(generate_manifest(self.root, exclude=[MANIFEST_NAME]),
                       manifest_path)
        os.remove(os.path.join(self.root, "link"))
        os.symlink("b.txt", os.path.join(self.root, "link"))
        report = verify_manifest(self.root, manifest_path)
        self.assertEqual([p for p, _ in report.modified], ["link"])

    def test_symlink_replaced_by_file_detected(self):
        write(os.path.join(self.root, "a.txt"), "a")
        os.symlink("a.txt", os.path.join(self.root, "link"))
        manifest_path = os.path.join(self.root, MANIFEST_NAME)
        write_manifest(generate_manifest(self.root, exclude=[MANIFEST_NAME]),
                       manifest_path)
        os.remove(os.path.join(self.root, "link"))
        write(os.path.join(self.root, "link"), "a")
        report = verify_manifest(self.root, manifest_path)
        self.assertEqual(report.modified[0][0], "link")
        self.assertIn("type changed", report.modified[0][1])

    def test_dangling_symlink_recorded_and_verifies(self):
        os.symlink("does-not-exist", os.path.join(self.root, "dangling"))
        manifest_path, report = make_and_verify(self.root)
        self.assertTrue(report.ok)
        self.assertEqual(report.unchanged, ["dangling"])

    def test_symlink_loop_does_not_break_scan(self):
        os.mkdir(os.path.join(self.root, "loopdir"))
        os.symlink("loopdir", os.path.join(self.root, "loopdir", "self"))
        manifest_path, report = make_and_verify(self.root)
        self.assertTrue(report.ok)
        self.assertEqual(report.unchanged, ["loopdir/self"])


class LargeFileTests(unittest.TestCase):
    def test_large_file_hashed_in_chunks(self):
        with tempfile.TemporaryDirectory() as root:
            big = os.path.join(root, "big.bin")
            payload = (bytes(range(256)) * (5 * 1024))  # 1.25 MiB, > chunk math
            with open(big, "wb") as handle:
                handle.write(payload * 2)  # 2.5 MiB
            manifest_path = os.path.join(root, MANIFEST_NAME)
            write_manifest(generate_manifest(root, exclude=[MANIFEST_NAME]),
                           manifest_path)
            report = verify_manifest(root, manifest_path)
            self.assertTrue(report.ok)
            with open(big, "r+b") as handle:
                handle.seek(2_000_000)
                handle.write(b"\x00")
            report = verify_manifest(root, manifest_path)
            self.assertEqual([p for p, _ in report.modified], ["big.bin"])


class ManifestSelfCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        write(os.path.join(self.root, "a.txt"), "hello")
        self.manifest_path, _ = make_and_verify(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_signature_ok_when_untouched(self):
        ok, detail, _ = verify_manifest_self(self.manifest_path)
        self.assertTrue(ok, detail)

    def _tamper(self, mutate):
        with open(self.manifest_path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        lines = mutate(lines)
        with open(self.manifest_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")

    def test_tampered_digest_detected(self):
        def mutate(lines):
            for i, line in enumerate(lines):
                obj = json.loads(line)
                if obj.get("kind") == "entry":
                    obj["digest"] = "0" * len(obj["digest"])
                    lines[i] = json.dumps(obj, sort_keys=True, separators=(",", ":"))
            return lines
        self._tamper(mutate)
        ok, _, _ = verify_manifest_self(self.manifest_path)
        self.assertFalse(ok)
        report = verify_manifest(self.root, self.manifest_path)
        self.assertFalse(report.manifest_ok)
        self.assertFalse(report.ok)

    def test_tampered_added_entry_detected(self):
        def mutate(lines):
            fake = {"kind": "entry", "path": "evil.txt", "type": "file",
                    "size": 0, "digest": "x"}
            lines.insert(1, json.dumps(fake, sort_keys=True, separators=(",", ":")))
            return lines
        self._tamper(mutate)
        ok, _, _ = verify_manifest_self(self.manifest_path)
        self.assertFalse(ok)

    def test_truncated_manifest_detected(self):
        with open(self.manifest_path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        ok, detail, manifest = verify_manifest_self(self.manifest_path)
        self.assertFalse(ok)
        self.assertIsNone(manifest)
        self.assertIn("malformed", detail)

    def test_traversal_path_in_manifest_rejected(self):
        def mutate(lines):
            for i, line in enumerate(lines):
                obj = json.loads(line)
                if obj.get("kind") == "entry":
                    obj["path"] = "../escape"
                    lines[i] = json.dumps(obj, sort_keys=True, separators=(",", ":"))
            return lines
        self._tamper(mutate)
        with self.assertRaises(UnsafePathError):
            Manifest.load(self.manifest_path)

    def test_malformed_manifest_raises(self):
        with open(self.manifest_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"kind": "entry", "path": "x",
                                     "type": "file", "size": 0, "digest": "d"}) + "\n")
        with self.assertRaises(ManifestFormatError):
            Manifest.load(self.manifest_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
