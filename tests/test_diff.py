"""对拍测试：同一批输入，重构前后输出必须逐字节一致。

- legacy_pipeline.process  为重构前基准；
- Pipeline(default_stages) 为重构后实现；
- 同时与已签入的 testdata/expected/* 基准数据比对。
"""
import os
import subprocess
import sys
import tempfile
import unittest

from pipeline import Context, EffectStore, Pipeline
from pipeline import stages
import legacy_pipeline


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTDATA = os.path.join(REPO_ROOT, "testdata")
CASES = ["case_normal", "case_edge", "case_empty"]
RUN_ID = "baseline"


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class DiffTest(unittest.TestCase):
    def _check_case(self, case):
        input_path = os.path.join(TESTDATA, case + ".csv")
        with tempfile.TemporaryDirectory() as tmp:
            legacy_report = os.path.join(tmp, "legacy.report")
            legacy_audit = os.path.join(tmp, "legacy.audit")
            new_report = os.path.join(tmp, "new.report")
            new_audit = os.path.join(tmp, "new.audit")

            legacy_pipeline.process(
                input_path, legacy_report, legacy_audit, RUN_ID
            )

            ctx = Context(
                data={
                    "input_path": input_path,
                    "output_path": new_report,
                    "audit_path": new_audit,
                    "run_id": RUN_ID,
                },
                effects=EffectStore(os.path.join(tmp, "effects.journal")),
            )
            Pipeline(stages.default_stages()).run(ctx)

            self.assertEqual(
                read(new_report),
                read(legacy_report),
                "report mismatch for %s" % case,
            )
            self.assertEqual(
                read(new_audit),
                read(legacy_audit),
                "audit mismatch for %s" % case,
            )
            self.assertEqual(
                read(new_report),
                read(os.path.join(TESTDATA, "expected", case + ".report")),
            )
            self.assertEqual(
                read(new_audit),
                read(os.path.join(TESTDATA, "expected", case + ".audit")),
            )

    def test_case_normal(self):
        self._check_case("case_normal")

    def test_case_edge(self):
        self._check_case("case_edge")

    def test_case_empty(self):
        self._check_case("case_empty")

    def test_cli_outputs_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(TESTDATA, "case_edge.csv")
            legacy_report = os.path.join(tmp, "legacy.report")
            legacy_audit = os.path.join(tmp, "legacy.audit")
            new_report = os.path.join(tmp, "new.report")
            new_audit = os.path.join(tmp, "new.audit")

            subprocess.run(
                [
                    sys.executable,
                    os.path.join(REPO_ROOT, "legacy_pipeline.py"),
                    input_path,
                    legacy_report,
                    "--audit", legacy_audit,
                    "--run-id", RUN_ID,
                ],
                check=True,
                cwd=REPO_ROOT,
            )
            subprocess.run(
                [
                    sys.executable,
                    os.path.join(REPO_ROOT, "run_pipeline.py"),
                    input_path,
                    new_report,
                    "--audit", new_audit,
                    "--run-id", RUN_ID,
                ],
                check=True,
                cwd=REPO_ROOT,
                stderr=subprocess.DEVNULL,
            )
            self.assertEqual(read(new_report), read(legacy_report))
            self.assertEqual(read(new_audit), read(legacy_audit))


if __name__ == "__main__":
    unittest.main()
