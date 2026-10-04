"""对拍：合法输入下重构后管线 == legacy 管线 == data/expected_output.jsonl。

三方对拍，确保校验与隔离没有改变合法输入的处理结果。
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

import legacy_pipeline
from pipeline import InputRejected, run_batch

ROOT = _bootstrap.ROOT
DATA = Path(ROOT) / "data"


def _load(name):
    with open(DATA / name, encoding="utf-8") as fh:
        return json.load(fh)


def _read_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


class TestParity(unittest.TestCase):
    def setUp(self):
        self.payload = _load("valid_orders.json")
        self.golden = _read_jsonl(DATA / "expected_output.jsonl")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmpdir = Path(self.tmp.name)

    def test_refactored_matches_legacy_runtime(self):
        legacy_path = self.tmpdir / "legacy.jsonl"
        new_path = self.tmpdir / "new.jsonl"
        legacy_count = legacy_pipeline.run_batch(self.payload["orders"], str(legacy_path))
        outcome = run_batch(self.payload, str(new_path))
        self.assertEqual(len(outcome.records), legacy_count)
        self.assertEqual(outcome.failures, [])
        self.assertEqual(_read_jsonl(legacy_path), _read_jsonl(new_path))

    def test_refactored_matches_golden(self):
        new_path = self.tmpdir / "new.jsonl"
        outcome = run_batch(self.payload, str(new_path))
        self.assertEqual(outcome.records, self.golden)
        self.assertEqual(_read_jsonl(new_path), self.golden)

    def test_per_order_fields_equal(self):
        outcome = run_batch(self.payload, str(self.tmpdir / "x.jsonl"))
        for got, expected in zip(outcome.records, self.golden):
            self.assertEqual(got["order_id"], expected["order_id"])
            for field in ("subtotal", "discount", "total", "tax"):
                self.assertEqual(got[field], expected[field],
                                 "order %s field %s differs" % (got["order_id"], field))

    def test_valid_input_is_never_rejected(self):
        try:
            run_batch(self.payload, str(self.tmpdir / "x.jsonl"))
        except InputRejected:
            self.fail("合法输入不应被校验拒绝")


if __name__ == "__main__":
    unittest.main()
