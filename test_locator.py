#!/usr/bin/env python3
"""Self-tests for payload_locator.py (unittest, standard library only)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

import make_samples
import payload_locator as pl

HERE = os.path.dirname(os.path.abspath(__file__))


class SampleFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.paths = make_samples.build_all(cls.tmp.name)
        cls.data = {}
        for name, path in cls.paths.items():
            with open(path, "rb") as fh:
                cls.data[name] = fh.read()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def scan(self, name, formats=None):
        return pl.scan(self.data[name], formats)


class ValidSamples(SampleFixture):
    def test_ok_single_valid_candidate(self):
        cands = self.scan("ok")
        self.assertEqual(len(cands), 1)
        cand = cands[0]
        self.assertEqual(cand.status, pl.STATUS_VALID)
        self.assertEqual(cand.score, 100)
        self.assertEqual(cand.offset, len(make_samples.shell_stub(1)))

    def test_padding_tolerated_and_offset_exact(self):
        cands = self.scan("padded")
        valid = pl.best_valid(cands)
        self.assertIsNotNone(valid)
        expected = len(make_samples.shell_stub(2, 512)) + 5009
        self.assertEqual(valid.offset, expected)
        self.assertEqual(valid.payload_offset, expected + pl.GSPK_HEADER_SIZE)

    def test_arbitrary_padding_lengths(self):
        for pad in (0, 1, 7, 4096, 65537):
            data = make_samples.shell_stub(1, 64) + b"\x00" * pad \
                + pl.build_gspk(make_samples.PAYLOAD)
            cands = pl.scan(data, ["gspk"])
            valid = pl.best_valid(cands)
            self.assertIsNotNone(valid, "pad=%d" % pad)
            self.assertEqual(valid.offset, len(make_samples.shell_stub(1, 64)) + pad)

    def test_extract_roundtrip(self):
        cands = self.scan("ok")
        valid = pl.best_valid(cands)
        payload = pl.extract_payload(self.data["ok"], valid)
        self.assertEqual(payload, make_samples.PAYLOAD)


class AbsenceAndTruncation(SampleFixture):
    def test_no_payload_reports_nothing(self):
        cands = self.scan("no_payload")
        self.assertEqual(cands, [])

    def test_truncated_payload_still_located(self):
        cands = self.scan("truncated_gspk", ["gspk"])
        self.assertEqual(len(cands), 1)
        cand = cands[0]
        self.assertEqual(cand.status, pl.STATUS_TRUNCATED)
        self.assertTrue(cand.detail["header_crc_ok"])
        self.assertGreater(cand.payload_length, cand.detail["payload_available"])
        self.assertTrue(any("truncated" in r for r in cand.reasons))

    def test_partial_header_at_eof_rejected(self):
        cands = self.scan("partial_header", ["gspk"])
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0].status, pl.STATUS_REJECTED)
        self.assertTrue(any("header truncated" in r for r in cands[0].reasons))


class FalsePositiveRejection(SampleFixture):
    def test_signature_inside_data_rejected_with_reason(self):
        cands = self.scan("sig_inside_gspk", ["gspk"])
        self.assertEqual(len(cands), 2)
        decoy, real = cands[-1], pl.best_valid(cands)
        self.assertIsNotNone(real)
        self.assertEqual(decoy.status, pl.STATUS_REJECTED)
        self.assertEqual(decoy.offset, 100)
        self.assertTrue(any("CRC32 mismatch" in r for r in decoy.reasons),
                        decoy.reasons)

    def test_zip_signature_inside_data_rejected(self):
        cands = self.scan("zip_sig_inside", ["zip"])
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0].status, pl.STATUS_REJECTED)
        self.assertTrue(any("implausible" in r for r in cands[0].reasons))

    def test_decoy_with_valid_header_crc_exposed_by_payload_crc(self):
        cands = self.scan("decoy_header", ["gspk"])
        self.assertEqual(len(cands), 2)
        decoy = [c for c in cands if c.status == pl.STATUS_REJECTED]
        self.assertEqual(len(decoy), 1)
        self.assertTrue(decoy[0].detail["header_crc_ok"])
        self.assertFalse(decoy[0].detail["payload_crc_ok"])
        self.assertTrue(any("payload CRC32 mismatch" in r
                            for r in decoy[0].reasons))
        self.assertIsNotNone(pl.best_valid(cands))


class Tampering(SampleFixture):
    def test_tampered_shell_does_not_hide_payload(self):
        cands = self.scan("tampered_shell", ["gspk"])
        valid = pl.best_valid(cands)
        self.assertIsNotNone(valid)
        self.assertEqual(valid.score, 100)

    def test_tampered_payload_rejected(self):
        cands = self.scan("tampered_payload", ["gspk"])
        self.assertEqual(len(cands), 1)
        cand = cands[0]
        self.assertEqual(cand.status, pl.STATUS_REJECTED)
        self.assertTrue(cand.detail["header_crc_ok"])
        self.assertFalse(cand.detail["payload_crc_ok"])


class MultiCandidateRanking(SampleFixture):
    def test_all_candidates_reported_and_sorted(self):
        cands = self.scan("multi", ["gspk"])
        self.assertEqual(len(cands), 3)
        scores = [c.score for c in cands]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(cands[0].status, pl.STATUS_VALID)
        self.assertEqual(cands[0].score, 100)
        for loser in cands[1:]:
            self.assertEqual(loser.status, pl.STATUS_REJECTED)
            self.assertTrue(loser.reasons)


class ZipSupport(SampleFixture):
    def test_zip_sfx_valid(self):
        # One hit at the archive start; per-member local headers deeper in
        # the archive are also signature hits and must be listed but rejected.
        cands = self.scan("zip_sfx", ["zip"])
        self.assertGreaterEqual(len(cands), 2)
        cand = cands[0]
        self.assertEqual(cand.status, pl.STATUS_VALID)
        self.assertEqual(cand.score, 100)
        self.assertEqual(cand.detail["members"],
                         ["readme.txt", "data/blob.bin"])
        self.assertEqual(cand.offset,
                         len(make_samples.shell_stub(11)) + 233)
        for inner in cands[1:]:
            self.assertEqual(inner.status, pl.STATUS_REJECTED)

    def test_zip_truncated(self):
        cands = self.scan("zip_truncated", ["zip"])
        self.assertGreaterEqual(len(cands), 1)
        for cand in cands:
            self.assertEqual(cand.status, pl.STATUS_TRUNCATED)
            self.assertTrue(any("truncated" in r for r in cand.reasons))


class CliTests(SampleFixture):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, os.path.join(HERE, "payload_locator.py"), *args],
            capture_output=True, text=True)

    def test_cli_exit_code_and_output(self):
        proc = self.run_cli(self.paths["ok"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("verdict: payload at offset", proc.stdout)

    def test_cli_exit_code_when_nothing_valid(self):
        proc = self.run_cli(self.paths["no_payload"])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no payload signature found", proc.stdout)

    def test_cli_json_lists_all_candidates(self):
        proc = self.run_cli("--json", self.paths["multi"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        cands = json.loads(proc.stdout)
        self.assertEqual(len(cands), 3)
        self.assertEqual(cands[0]["status"], "valid")

    def test_cli_extract(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "payload.bin")
            cands = self.scan("ok")
            off = pl.best_valid(cands).offset
            proc = self.run_cli("--extract", str(off), "-o", out,
                                self.paths["ok"])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(out, "rb") as fh:
                self.assertEqual(fh.read(), make_samples.PAYLOAD)

    def test_cli_refuses_rejected_candidate(self):
        cands = self.scan("tampered_payload", ["gspk"])
        proc = self.run_cli("--extract", str(cands[0].offset),
                            self.paths["tampered_payload"])
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
