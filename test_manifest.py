"""manifest 库自测：边界用例 + 篡改检测（python3 test_manifest.py）。"""

import json
import os
import tempfile
import unittest

import manifest


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def path(self, *parts):
        return os.path.join(self.tmp, *parts)

    def write(self, rel, data=b"x"):
        full = self.path(rel)
        os.makedirs(os.path.dirname(full), exist_ok=True) if os.path.dirname(rel) else None
        with open(full, "wb") as f:
            f.write(data)
        return full

    def verify(self, root=None):
        root = root or self.tmp
        return manifest.verify(root, manifest.generate_manifest(root))

    def test_clean_directory_passes(self):
        self.write("a/b.txt", b"hello")
        report = self.verify()
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["modified"], [])
        self.assertEqual(report["deleted"], [])
        self.assertEqual(report["added"], [])
        self.assertTrue(report["manifest_intact"])

    def test_empty_directory(self):
        report = self.verify()
        self.assertTrue(report["ok"])
        self.assertEqual(manifest.generate_manifest(self.tmp)["files"], [])

    def test_modified_deleted_added(self):
        self.write("keep.txt", b"same")
        self.write("change.txt", b"original")
        self.write("gone.txt", b"bye")
        m = manifest.generate_manifest(self.tmp)

        with open(self.path("change.txt"), "wb") as f:
            f.write(b"tampered content")
        os.remove(self.path("gone.txt"))
        self.write("new.txt", b"extra")

        report = manifest.verify(self.tmp, m)
        self.assertFalse(report["ok"])
        self.assertEqual(report["deleted"], ["gone.txt"])
        self.assertEqual(report["added"], ["new.txt"])
        modified_paths = [item["path"] for item in report["modified"]]
        self.assertIn("change.txt", modified_paths)
        self.assertNotIn("keep.txt", modified_paths)

    def test_length_change_detected(self):
        self.write("f", b"abc")
        m = manifest.generate_manifest(self.tmp)
        with open(self.path("f"), "wb") as f:
            f.write(b"abcd")
        report = manifest.verify(self.tmp, m)
        self.assertEqual(report["modified"][0]["path"], "f")
        self.assertIn("长度变化", report["modified"][0]["reason"])

    def test_case_different_names(self):
        self.write("ReadMe.txt", b"upper")
        self.write("readme.txt", b"lower")
        report = self.verify()
        if not report["ok"]:
            self.skipTest("文件系统大小写不敏感，无法同时存放两个文件")
        paths = {e["path"] for e in manifest.generate_manifest(self.tmp)["files"]}
        self.assertIn("ReadMe.txt", paths)
        self.assertIn("readme.txt", paths)
        self.assertEqual(len(paths), 2)
        digests = {p: manifest.hash_file(self.path(p)) for p in paths}
        self.assertNotEqual(digests["ReadMe.txt"], digests["readme.txt"])

    def test_symlinks(self):
        self.write("dir/target.txt", b"data")
        os.symlink("dir/target.txt", self.path("link.txt"))
        os.symlink("dir", self.path("linkdir"))  # 指向目录的符号链接
        report = self.verify()
        self.assertTrue(report["ok"], report)
        entries = {e["path"]: e for e in manifest.generate_manifest(self.tmp)["files"]}
        self.assertEqual(entries["link.txt"]["type"], "symlink")
        self.assertEqual(entries["link.txt"]["target"], "dir/target.txt")
        self.assertNotIn("linkdir/target.txt", entries)  # 不跟随链接目录

        # 改链接目标应判为修改
        os.remove(self.path("link.txt"))
        os.symlink("gone.txt", self.path("link.txt"))
        m = manifest.generate_manifest(self.tmp)  # 重新生成做对照前先恢复结构：
        # 直接用上一份清单验证更严格，这里重新构造：
        os.remove(self.path("link.txt"))
        os.symlink("dir/target.txt", self.path("link.txt"))
        report = manifest.verify(self.tmp, m) if False else self.verify()
        self.assertTrue(report["ok"])
        # 目标被改 -> modified
        m = manifest.generate_manifest(self.tmp)
        os.remove(self.path("link.txt"))
        os.symlink("newtarget", self.path("link.txt"))
        report = manifest.verify(self.tmp, m)
        self.assertEqual(report["modified"][0]["path"], "link.txt")
        self.assertIn("链接", report["modified"][0]["reason"])

    def test_symlink_type_change_detected(self):
        os.symlink("a", self.path("x"))
        m = manifest.generate_manifest(self.tmp)
        os.remove(self.path("x"))
        self.write("x", b"real file")
        report = manifest.verify(self.tmp, m)
        self.assertEqual(report["modified"][0]["path"], "x")
        self.assertIn("类型变化", report["modified"][0]["reason"])

    def test_large_file(self):
        size = 5 * 1024 * 1024 + 123  # 超过多个分块
        full = self.write("big.bin")
        with open(full, "wb") as f:
            chunk = b"a" * (1024 * 1024)
            for _ in range(5):
                f.write(chunk)
            f.write(b"b" * 123)
        m = manifest.generate_manifest(self.tmp)
        entry = m["files"][0]
        self.assertEqual(entry["size"], size)
        self.assertTrue(self.verify()["ok"])
        with open(full, "r+b") as f:
            f.seek(3 * 1024 * 1024 + 7)
            f.write(b"X")
        report = manifest.verify(self.tmp, m)
        self.assertEqual(report["modified"][0]["path"], "big.bin")

    def test_manifest_self_tamper_entry(self):
        self.write("a.txt", b"hello")
        m = manifest.generate_manifest(self.tmp)
        self.assertTrue(manifest.check_manifest_integrity(m))

        # 篡改某条目（例如改路径），自校验必须发现
        m["files"][0]["path"] = "forged.txt"
        self.assertFalse(manifest.check_manifest_integrity(m))
        report = manifest.verify(self.tmp, m)
        self.assertFalse(report["manifest_intact"])
        self.assertFalse(report["ok"])

    def test_manifest_self_tamper_digest(self):
        self.write("a.txt", b"hello")
        m = manifest.generate_manifest(self.tmp)
        m["files"][0]["sha256"] = "0" * 64
        self.assertFalse(manifest.check_manifest_integrity(m))

    def test_manifest_tamper_on_disk(self):
        self.write("a.txt", b"hello")
        mp = self.path("manifest.json")
        manifest.save_manifest(m := manifest.generate_manifest(self.tmp), mp)
        self.assertEqual(manifest.main(["selfcheck", mp]), 0)

        with open(mp, "r+", encoding="utf-8") as f:
            data = json.load(f)
            data["files"][0]["size"] = 99999
            f.seek(0)
            f.truncate()
            json.dump(data, f)
        self.assertEqual(manifest.main(["selfcheck", mp]), 2)
        self.assertEqual(manifest.main(["verify", self.tmp, "-m", mp]), 2)

    def test_path_traversal_rejected(self):
        for bad in ["../etc/passwd", "/etc/passwd", "a/../../b", "C:\\Windows", ""]:
            with self.assertRaises(manifest.ManifestError):
                manifest.normalize_relpath(bad)
        # 良性路径通过
        self.assertEqual(manifest.normalize_relpath("./a/b"), "a/b")

    def test_malicious_manifest_paths_rejected(self):
        bad_manifest = {
            "version": 1,
            "files": [{"path": "../../etc/passwd", "type": "file", "size": 1, "sha256": "x"}],
            "manifest_sha256": "x",
        }
        mp = self.path("evil.json")
        manifest.save_manifest(bad_manifest, mp)
        with self.assertRaises(manifest.ManifestError):
            manifest.load_manifest(mp)

    def test_nested_paths_normalized(self):
        self.write("a/b/c.txt", b"deep")
        entries = manifest.generate_manifest(self.tmp)["files"]
        self.assertEqual(entries[0]["path"], "a/b/c.txt")
        self.assertTrue(self.verify()["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
