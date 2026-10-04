"""重构后流水线的回归测试。

覆盖：单阶段执行、全阶段执行、中途失败重试、断点续跑、阶段跳过、
以及副作用幂等断言。
"""
import os
import tempfile
import unittest

from pipeline import Context, EffectStore, Hook, Pipeline, PipelineError, Stage
from pipeline import stages


TESTDATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "testdata")


class RecordingHook(Hook):
    def __init__(self):
        self.events = []

    def on_stage_start(self, name, ctx):
        self.events.append(("start", name))

    def on_stage_retry(self, name, ctx, attempt, error):
        self.events.append(("retry", name, attempt))

    def on_stage_success(self, name, ctx, attempts):
        self.events.append(("success", name, attempts))

    def on_stage_skip(self, name, ctx, reason):
        self.events.append(("skip", name, reason))

    def on_stage_failure(self, name, ctx, error):
        self.events.append(("failure", name, error.attempts))


class Flaky:
    """前 failures 次调用抛异常，之后正常执行原阶段函数。"""

    def __init__(self, fn, failures):
        self.fn = fn
        self.remaining = failures
        self.calls = 0

    def __call__(self, ctx):
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise RuntimeError("injected failure")
        return self.fn(ctx)


class FailAfterEffect:
    """先执行原阶段（副作用已产生），再抛异常，模拟“写完后崩溃”。"""

    def __init__(self, fn, failures):
        self.fn = fn
        self.remaining = failures
        self.calls = 0

    def __call__(self, ctx):
        self.calls += 1
        self.fn(ctx)
        if self.remaining > 0:
            self.remaining -= 1
            raise RuntimeError("injected failure after side effect")


def make_env(tmp, case="case_normal", run_id="baseline"):
    input_path = os.path.join(TESTDATA, case + ".csv")
    output_path = os.path.join(tmp, case + ".report")
    audit_path = os.path.join(tmp, case + ".audit")
    journal_path = os.path.join(tmp, case + ".journal")
    checkpoint_path = os.path.join(tmp, case + ".checkpoint")
    ctx = Context(
        data={
            "input_path": input_path,
            "output_path": output_path,
            "audit_path": audit_path,
            "run_id": run_id,
        },
        effects=EffectStore(journal_path),
    )
    return ctx, output_path, audit_path, journal_path, checkpoint_path


def expected_report(case):
    with open(os.path.join(TESTDATA, "expected", case + ".report"), encoding="utf-8") as fh:
        return fh.read()


class PipelineStagesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_single_stage_only_load(self):
        ctx, output_path, audit_path, _, _ = make_env(self.tmpdir)
        hook = RecordingHook()
        pipe = Pipeline(stages.default_stages(), hooks=[hook])
        pipe.run(ctx, only={"load"})

        self.assertEqual(len(ctx.data["records"]), 5)
        self.assertEqual(ctx.data["skipped"], 0)
        self.assertNotIn("totals", ctx.data)
        self.assertFalse(os.path.exists(output_path))
        self.assertFalse(os.path.exists(audit_path))
        self.assertIn(("start", "load"), hook.events)
        self.assertIn(("success", "load", 1), hook.events)
        self.assertIn(("skip", "normalize", "not-selected"), hook.events)
        self.assertIn(("skip", "audit", "not-selected"), hook.events)

    def test_all_stages_output_matches_expected(self):
        ctx, output_path, audit_path, journal_path, checkpoint_path = make_env(self.tmpdir)
        hook = RecordingHook()
        pipe = Pipeline(
            stages.default_stages(), hooks=[hook], checkpoint_path=checkpoint_path
        )
        pipe.run(ctx)

        with open(output_path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), expected_report("case_normal"))
        with open(audit_path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "run=baseline processed=5 skipped=0\n")
        names = [event[1] for event in hook.events if event[0] == "success"]
        self.assertEqual(names, ["load", "normalize", "aggregate", "report", "audit"])
        with open(checkpoint_path, encoding="utf-8") as fh:
            self.assertEqual(
                [line.strip() for line in fh if line.strip()],
                ["load", "normalize", "aggregate", "report", "audit"],
            )
        ctx.effects.assert_idempotent()

    def test_mid_pipeline_flaky_stage_retried(self):
        ctx, output_path, _, _, _ = make_env(self.tmpdir)
        hook = RecordingHook()
        flaky_aggregate = Flaky(stages.aggregate_stage, failures=2)
        stage_list = [
            Stage("load", stages.load_stage),
            Stage("normalize", stages.normalize_stage),
            Stage("aggregate", flaky_aggregate),
            Stage("report", stages.report_stage),
            Stage("audit", stages.audit_stage),
        ]
        pipe = Pipeline(stage_list, hooks=[hook], max_attempts=3)
        pipe.run(ctx)

        self.assertEqual(flaky_aggregate.calls, 3)
        self.assertIn(("retry", "aggregate", 2), hook.events)
        self.assertIn(("retry", "aggregate", 3), hook.events)
        self.assertIn(("success", "aggregate", 3), hook.events)
        with open(output_path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), expected_report("case_normal"))
        ctx.effects.assert_idempotent()

    def test_side_effect_not_duplicated_when_stage_retried(self):
        ctx, output_path, _, journal_path, _ = make_env(self.tmpdir)
        write_calls = []
        original_write = stages._write_file

        def counting_write(path, content):
            write_calls.append(path)
            return original_write(path, content)

        stages._write_file = counting_write
        try:
            flaky_report = FailAfterEffect(stages.report_stage, failures=1)
            stage_list = [
                Stage("load", stages.load_stage),
                Stage("normalize", stages.normalize_stage),
                Stage("aggregate", stages.aggregate_stage),
                Stage("report", flaky_report),
                Stage("audit", stages.audit_stage),
            ]
            hook = RecordingHook()
            pipe = Pipeline(stage_list, hooks=[hook], max_attempts=3)
            pipe.run(ctx)
        finally:
            stages._write_file = original_write

        self.assertEqual(flaky_report.calls, 2)
        self.assertEqual(write_calls, [output_path])
        with open(output_path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), expected_report("case_normal"))
        with open(journal_path, encoding="utf-8") as fh:
            keys = [line.strip() for line in fh if line.strip()]
        self.assertEqual(keys.count("write-report:%s" % output_path), 1)
        ctx.effects.assert_idempotent()

    def test_failure_exhausts_retries_and_pipeline_resumes(self):
        ctx, output_path, audit_path, _, checkpoint_path = make_env(self.tmpdir)
        always_fail = Flaky(stages.normalize_stage, failures=10**9)
        broken_stages = [
            Stage("load", stages.load_stage),
            Stage("normalize", always_fail),
        ] + [
            Stage(name, fn)
            for name, fn in [
                ("aggregate", stages.aggregate_stage),
                ("report", stages.report_stage),
                ("audit", stages.audit_stage),
            ]
        ]
        pipe = Pipeline(broken_stages, max_attempts=2, checkpoint_path=checkpoint_path)
        with self.assertRaises(PipelineError) as caught:
            pipe.run(ctx)
        self.assertEqual(caught.exception.stage_name, "normalize")
        self.assertEqual(caught.exception.attempts, 2)
        with open(checkpoint_path, encoding="utf-8") as fh:
            completed = [line.strip() for line in fh if line.strip()]
        self.assertEqual(completed, ["load"])

        ctx2, _, _, _, _ = make_env(self.tmpdir)
        hook = RecordingHook()
        resumed = Pipeline(
            stages.default_stages(),
            hooks=[hook],
            max_attempts=3,
            checkpoint_path=checkpoint_path,
        )
        resumed.run(ctx2)

        self.assertIn(("skip", "load", "completed"), hook.events)
        self.assertNotIn(("start", "load"), hook.events)
        self.assertIn(("success", "normalize", 1), hook.events)
        with open(output_path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), expected_report("case_normal"))
        with open(audit_path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "run=baseline processed=5 skipped=0\n")

    def test_stage_explicitly_skipped(self):
        ctx, output_path, audit_path, _, _ = make_env(self.tmpdir)
        hook = RecordingHook()
        pipe = Pipeline(stages.default_stages(), hooks=[hook])
        pipe.run(ctx, skip={"audit"})

        self.assertIn(("skip", "audit", "skipped"), hook.events)
        self.assertFalse(os.path.exists(audit_path))
        with open(output_path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), expected_report("case_normal"))
        for event in hook.events:
            if event[0] == "start":
                self.assertNotEqual(event[1], "audit")

    def test_empty_input_boundary_case(self):
        ctx, output_path, audit_path, _, _ = make_env(self.tmpdir, case="case_empty")
        Pipeline(stages.default_stages()).run(ctx)
        with open(output_path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), expected_report("case_empty"))
        with open(audit_path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "run=baseline processed=0 skipped=0\n")

    def test_effect_store_is_idempotent(self):
        journal_path = os.path.join(self.tmpdir, "journal")
        store = EffectStore(journal_path)
        counter = {"n": 0}

        def effect():
            counter["n"] += 1

        self.assertTrue(store.apply("key-1", effect))
        self.assertFalse(store.apply("key-1", effect))
        self.assertFalse(store.apply("key-1", effect))
        self.assertEqual(counter["n"], 1)
        self.assertTrue(store.applied("key-1"))
        store.assert_idempotent()

        reloaded = EffectStore(journal_path)
        self.assertFalse(reloaded.apply("key-1", effect))
        self.assertEqual(counter["n"], 1)
        reloaded.assert_idempotent()


if __name__ == "__main__":
    unittest.main()
