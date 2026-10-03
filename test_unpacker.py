"""Self-tests for unpacker.py. Run: python3 -m unittest -v test_unpacker

Everything is built in-memory; nothing on disk is executed. One test
statically audits unpacker.py to prove it cannot run the analyzed file.
"""

import ast
import io
import os
import random
import tempfile
import unittest

import unpacker

SHELL = b"#!/bin/sh\necho stub\nexit 0\n"
PAYLOAD = b"payload-bytes\x00\x01\x02" * 40


def pad(n, seed=7):
    rng = random.Random(seed)
    return bytes(rng.randrange(256) for _ in range(n))


class LocateTests(unittest.TestCase):
    def test_clean_archive(self):
        data = unpacker.build_archive(SHELL, PAYLOAD)
        result = unpacker.analyze(data)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["best"]["score"], unpacker.MAX_SCORE)
        off, payload = unpacker.extract(data)
        self.assertEqual(payload, PAYLOAD)
        self.assertEqual(off, len(SHELL) + unpacker.MAGIC_LEN
                         + unpacker.HEADER_LEN)

    def test_arbitrary_padding_offsets(self):
        for n in (0, 1, 7, 4096, 65537):
            data = unpacker.build_archive(SHELL, PAYLOAD, pad(n, seed=n))
            off, payload = unpacker.extract(data)
            self.assertEqual(payload, PAYLOAD)
            self.assertEqual(off, len(SHELL) + n + unpacker.MAGIC_LEN
                             + unpacker.HEADER_LEN)

    def test_no_payload(self):
        result = unpacker.analyze(SHELL + b"# nothing appended\n")
        self.assertEqual(result["status"], "no_candidate")
        self.assertEqual(result["candidates"], [])
        with self.assertRaises(unpacker.PayloadError):
            unpacker.extract(SHELL)

    def test_empty_file(self):
        result = unpacker.analyze(b"")
        self.assertEqual(result["status"], "no_candidate")

    def test_signature_inside_data_rejected(self):
        junk = pad(3000, seed=1)
        pos = 1234
        data = (junk[:pos] + unpacker.MAGIC
                + junk[pos + unpacker.MAGIC_LEN:])
        result = unpacker.analyze(data)
        self.assertEqual(result["status"], "invalid_candidates")
        self.assertEqual(len(result["candidates"]), 1)
        cand = result["candidates"][0]
        self.assertFalse(cand["extractable"])
        self.assertTrue(cand["failures"])  # reasons are recorded
        with self.assertRaises(unpacker.PayloadError):
            unpacker.extract(data)

    def test_signature_inside_payload(self):
        body = b"xx" + unpacker.MAGIC + b"yy" * 500
        data = unpacker.build_archive(SHELL, body, pad(64))
        result = unpacker.analyze(data)
        self.assertEqual(result["status"], "valid")
        self.assertGreaterEqual(len(result["candidates"]), 2)
        best = result["candidates"][0]
        self.assertTrue(best["extractable"])
        self.assertEqual(best["offset"], len(SHELL) + 64)
        embedded = [c for c in result["candidates"]
                    if c["offset"] == len(SHELL) + 64
                    + unpacker.MAGIC_LEN + unpacker.HEADER_LEN + 2]
        self.assertEqual(len(embedded), 1)
        self.assertFalse(embedded[0]["extractable"])
        self.assertTrue(embedded[0]["failures"])
        _, payload = unpacker.extract(data)
        self.assertEqual(payload, body)

    def test_footer_magic_is_also_a_candidate(self):
        data = unpacker.build_archive(SHELL, PAYLOAD)
        footer_at = len(data) - unpacker.FOOTER_LEN
        result = unpacker.analyze(data)
        hits = [c["offset"] for c in result["candidates"]]
        self.assertIn(footer_at, hits)
        footer_cand = next(c for c in result["candidates"]
                           if c["offset"] == footer_at)
        self.assertFalse(footer_cand["extractable"])

    def test_truncated_payload(self):
        data = unpacker.build_archive(SHELL, PAYLOAD, pad(32))[:-17]
        result = unpacker.analyze(data)
        self.assertEqual(result["status"], "invalid_candidates")
        cand = result["candidates"][0]
        failed = {c["name"] for c in cand["checks"] if not c["passed"]}
        self.assertTrue(failed & {"bounds", "footer", "crc32"})
        with self.assertRaises(unpacker.PayloadError):
            unpacker.extract(data)

    def test_truncated_header(self):
        full = unpacker.build_archive(SHELL, PAYLOAD)
        start = full.find(unpacker.MAGIC)
        data = full[:start + unpacker.MAGIC_LEN + 6]
        result = unpacker.analyze(data)
        self.assertEqual(result["status"], "invalid_candidates")
        cand = result["candidates"][0]
        names = {c["name"] for c in cand["checks"] if not c["passed"]}
        self.assertIn("header_complete", names)

    def test_shell_tampered_still_extracts_with_warning(self):
        data = bytearray(unpacker.build_archive(SHELL, PAYLOAD, pad(16)))
        data[3] ^= 0x40
        data = bytes(data)
        result = unpacker.analyze(data)
        self.assertEqual(result["status"], "valid_shell_modified")
        best = result["best"]
        self.assertTrue(best["extractable"])
        self.assertTrue(best["warnings"])
        prefix_check = next(c for c in best["checks"]
                            if c["name"] == "prefix_sha256")
        self.assertFalse(prefix_check["passed"])
        _, payload = unpacker.extract(data)
        self.assertEqual(payload, PAYLOAD)

    def test_payload_tampered_rejected(self):
        data = bytearray(unpacker.build_archive(SHELL, PAYLOAD))
        off, _ = unpacker.extract(bytes(data))
        data[off + 5] ^= 0xFF
        result = unpacker.analyze(bytes(data))
        self.assertEqual(result["status"], "invalid_candidates")
        cand = result["candidates"][0]
        failed = {c["name"] for c in cand["checks"] if not c["passed"]}
        self.assertIn("crc32", failed)
        # footer echo still matches the (untouched) header; crc32 alone
        # catches the corruption, which is exactly its job.
        passed = {c["name"] for c in cand["checks"] if c["passed"]}
        self.assertIn("footer", passed)
        with self.assertRaises(unpacker.PayloadError):
            unpacker.extract(bytes(data))

    def test_empty_payload_legal(self):
        data = unpacker.build_archive(SHELL, b"", pad(5))
        off, payload = unpacker.extract(data)
        self.assertEqual(payload, b"")

    def test_double_archive_reports_all(self):
        a = unpacker.build_archive(SHELL, b"AAA" * 100, b"\x00")
        b = unpacker.build_archive(b"#s2\n", b"BBB" * 100)
        result = unpacker.analyze(a + b)
        good = [c for c in result["candidates"] if c["extractable"]]
        self.assertEqual(len(good), 2)
        self.assertEqual(result["candidates"][0]["score"],
                         unpacker.MAX_SCORE)
        _, payload = unpacker.extract(a + b)
        self.assertEqual(payload, b"AAA" * 100)

    def test_candidates_sorted_and_all_listed(self):
        decoy = SHELL + unpacker.MAGIC + b"garbage" * 20
        real = unpacker.build_archive(b"", PAYLOAD, pad(33))
        result = unpacker.analyze(decoy + real)
        scores = [c["score"] for c in result["candidates"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertGreaterEqual(len(result["candidates"]), 3)
        self.assertTrue(result["candidates"][0]["extractable"])

    def test_overlapping_scan(self):
        # Two adjacent MAGICs must both be found (scan restarts at hit+1).
        data = unpacker.MAGIC + unpacker.MAGIC + pad(10)
        result = unpacker.analyze(data)
        offsets = [c["offset"] for c in result["candidates"]]
        self.assertIn(0, offsets)
        self.assertIn(unpacker.MAGIC_LEN, offsets)


class CliTests(unittest.TestCase):
    def _run_cli(self, argv, data):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "case.bin")
            with open(path, "wb") as fh:
                fh.write(data)
            out, err = io.StringIO(), io.StringIO()
            import contextlib
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                code = unpacker.main([path] + argv)
            return code, out.getvalue(), err.getvalue(), tmp

    def test_cli_human_lists_all_candidates(self):
        body = b"q" + unpacker.MAGIC + b"z" * 300
        data = unpacker.build_archive(SHELL, body)
        code, out, _, _ = self._run_cli([], data)
        self.assertEqual(code, 0)
        self.assertIn("candidates:", out)
        self.assertIn("#1", out)
        self.assertIn("#2", out)
        self.assertIn("payload: offset=", out)

    def test_cli_json_output(self):
        import json
        data = unpacker.build_archive(SHELL, PAYLOAD)
        code, out, _, _ = self._run_cli(["--json"], data)
        self.assertEqual(code, 0)
        parsed = json.loads(out)
        self.assertEqual(parsed["status"], "valid")
        self.assertTrue(parsed["candidates"])

    def test_cli_extract(self):
        data = unpacker.build_archive(SHELL, PAYLOAD, pad(9))
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "a.bin")
            dst = os.path.join(tmp, "out.bin")
            with open(src, "wb") as fh:
                fh.write(data)
            code = unpacker.main([src, "--extract", dst])
            self.assertEqual(code, 0)
            with open(dst, "rb") as fh:
                self.assertEqual(fh.read(), PAYLOAD)

    def test_cli_exit_codes(self):
        code, _, _, _ = self._run_cli([], SHELL)
        self.assertEqual(code, 2)  # no candidate
        bad = unpacker.build_archive(SHELL, PAYLOAD)[:-5]
        code, _, _, _ = self._run_cli([], bad)
        self.assertEqual(code, 3)  # candidates exist, none extractable


class NeverExecutesTests(unittest.TestCase):
    """Statically prove unpacker.py cannot execute the analyzed file."""

    FORBIDDEN_CALLS = {
        "eval", "exec", "compile", "system", "popen", "spawnl", "spawnv",
        "run", "call", "check_call", "check_output", "Popen",
    }
    FORBIDDEN_IMPORTS = {"subprocess", "pty", "commands", "ctypes"}

    def test_no_execution_primitives(self):
        import unpacker as mod
        with open(mod.__file__, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = ([a.name.split(".")[0] for a in node.names]
                         if isinstance(node, ast.Import)
                         else [node.module.split(".")[0]])
                for name in names:
                    self.assertNotIn(name, self.FORBIDDEN_IMPORTS,
                                     "forbidden import: %s" % name)
            elif isinstance(node, ast.Call):
                func = node.func
                attr = func.attr if isinstance(func, ast.Attribute) \
                    else getattr(func, "id", None)
                self.assertNotIn(attr, self.FORBIDDEN_CALLS,
                                 "forbidden call: %s" % attr)

    def test_read_only_file_access(self):
        import unpacker as mod
        with open(mod.__file__, "r", encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn('chmod', src)
        self.assertNotIn('os.exec', src)
        self.assertIn('"rb"', src)


if __name__ == "__main__":
    unittest.main()
