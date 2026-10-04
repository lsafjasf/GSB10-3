"""回归测试：单阶段、全阶段、中途失败重试、阶段跳过、幂等断言、对拍。"""
import os
import unittest

from legacy_pipeline import process_orders
from pipeline.effects import EffectSink, assert_idempotent
from pipeline.errors import TransientError
from pipeline.orders import (
    build_stages,
    render_outbox,
    render_report,
    run_orders,
)
from pipeline.runner import Context, Hooks, RetryPolicy, Runner, StageSpec
from pipeline import stages

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def load_fixture():
    with open(os.path.join(DATA_DIR, "orders.csv"), encoding="utf-8") as fh:
        return fh.readlines()


class RecordingHooks(Hooks):
    def __init__(self):
        self.events = []

    def before_stage(self, name, ctx):
        self.events.append(("before", name))

    def after_stage(self, name, ctx, result, attempts):
        self.events.append(("after", name, attempts))

    def on_stage_error(self, name, ctx, exc, attempt):
        self.events.append(("error", name, attempt, str(exc)))

    def on_stage_skipped(self, name, ctx):
        self.events.append(("skipped", name))


class FlakySink(EffectSink):
    """前 fail_after 次副作用生效后，在下一次 apply 时模拟崩溃（只崩一次）。"""

    def __init__(self, fail_after):
        super().__init__()
        self.fail_after = fail_after
        self._count = 0
        self._fired = False

    def apply(self, key, payload):
        if not self._fired:
            self._count += 1
            if self._count > self.fail_after:
                self._fired = True
                raise TransientError("simulated crash after partial side effects")
        return super().apply(key, payload)


class TestSingleStage(unittest.TestCase):
    """单阶段：每个阶段都能脱离流水线单独执行、单独断言。"""

    def test_parse_stage_alone(self):
        ctx = Context(input=["A1,alice,10,USD\n", "bad,row\n", "\n"])
        result = stages.parse_stage(ctx)
        self.assertEqual([r.order_id for r in result.rows], ["A1"])
        self.assertEqual([r.reason for r in result.rejects], ["bad_row"])
        self.assertEqual(result.rows[0].line_no, 1)

    def test_enrich_stage_alone_with_injected_input(self):
        ctx = Context(input=[])
        ctx.results["parse"] = stages.ParseResult(
            rows=[stages.Row("A1", "alice", stages.Decimal("10"), "USD", 1)]
        )
        ctx.results["validate"] = stages.ValidateResult(
            rows=list(ctx.results["parse"].rows)
        )
        result = stages.enrich_stage(ctx)
        self.assertEqual(str(result.orders[0].total_cny), "71.71")

    def test_single_stage_via_runner_with_own_retry_policy(self):
        calls = {"n": 0}

        def flaky(ctx):
            calls["n"] += 1
            if calls["n"] < 2:
                raise TransientError("boom")
            return "done"

        hooks = RecordingHooks()
        spec = StageSpec("solo", flaky, retry=RetryPolicy(max_attempts=3))
        ctx = Runner([spec], hooks=hooks).run(Context())
        self.assertEqual(ctx.results["solo"], "done")
        self.assertEqual(calls["n"], 2)
        self.assertEqual(ctx.trace[0].attempts, 2)
        self.assertIn(("error", "solo", 1, "boom"), hooks.events)


class TestFullPipeline(unittest.TestCase):
    """全阶段：端到端跑通，钩子在每个阶段边界被触发，中间结果齐全。"""

    def test_full_run_trace_and_hooks(self):
        hooks = RecordingHooks()
        ctx = run_orders(load_fixture(), hooks=hooks)
        self.assertEqual(
            [t.name for t in ctx.trace],
            ["parse", "validate", "enrich", "aggregate",
             "emit_report", "emit_notify"],
        )
        self.assertTrue(all(t.status == "ok" for t in ctx.trace))
        for name in ctx.results:
            self.assertIn(("before", name), hooks.events)
        self.assertEqual(
            sum(1 for e in hooks.events if e[0] == "after"), 6)

    def test_intermediate_results_are_explicit(self):
        ctx = run_orders(load_fixture())
        parsed = ctx.results["parse"]
        self.assertEqual(len(parsed.rows) + len(parsed.rejects), 13)
        report = ctx.results["aggregate"]
        self.assertEqual(len(report.accepted), 4)
        self.assertEqual(len(report.rejects), 9)
        self.assertEqual(str(report.total_cny), "2916.07")


class TestMidFailureRetry(unittest.TestCase):
    """中途失败重试：副作用阶段崩在半路，重试后结果一致且副作用不重复。"""

    def test_emit_crash_midway_then_retry(self):
        sink = FlakySink(fail_after=2)  # 生效 2 条副作用后崩溃
        hooks = RecordingHooks()
        ctx = run_orders(load_fixture(), hooks=hooks, sink=sink)

        emit_trace = [t for t in ctx.trace if t.name == "emit_report"][0]
        self.assertEqual(emit_trace.attempts, 2)
        self.assertIn(("error", "emit_report", 1,
                       "simulated crash after partial side effects"),
                      hooks.events)

        # 幂等断言：没有任何副作用键被真正执行两次
        sink.assert_no_duplicate_effects()
        # 最终输出与无故障运行逐字节一致
        clean = run_orders(load_fixture())
        self.assertEqual(render_report(ctx.sink), render_report(clean.sink))
        self.assertEqual(render_outbox(ctx.sink), render_outbox(clean.sink))

    def test_retry_exhaustion_raises_and_marks_failed(self):
        class AlwaysFailSink(EffectSink):
            def apply(self, key, payload):
                raise TransientError("always down")

        ctx = Context(input=["A1,a,1,CNY"], sink=AlwaysFailSink())
        with self.assertRaises(TransientError):
            Runner(build_stages()).run(ctx)
        emit_trace = [t for t in ctx.trace if t.name == "emit_report"][0]
        self.assertEqual(emit_trace.status, "failed")
        self.assertEqual(emit_trace.attempts, 3)

    def test_non_transient_error_is_not_retried(self):
        calls = {"n": 0}

        def broken(ctx):
            calls["n"] += 1
            raise ValueError("permanent")

        spec = StageSpec("parse", broken, retry=RetryPolicy(max_attempts=3))
        with self.assertRaises(ValueError):
            Runner([spec]).run(Context(input=[]))
        self.assertEqual(calls["n"], 1)


class TestStageSkipped(unittest.TestCase):
    """阶段被跳过：notify=False 时 emit_notify 整体跳过，其余输出不变。"""

    def test_notify_stage_skipped(self):
        hooks = RecordingHooks()
        ctx = run_orders(load_fixture(), options={"notify": False}, hooks=hooks)
        trace = {t.name: t for t in ctx.trace}
        self.assertEqual(trace["emit_notify"].status, "skipped")
        self.assertIn(("skipped", "emit_notify"), hooks.events)
        self.assertEqual(render_outbox(ctx.sink), "")
        # 报表不受跳过影响，与完整运行一致
        full = run_orders(load_fixture())
        self.assertEqual(render_report(ctx.sink), render_report(full.sink))


class TestIdempotency(unittest.TestCase):
    """幂等断言：阶段重跑/重试不得重复产生副作用。"""

    def test_emit_stage_rerun_is_idempotent(self):
        ctx = run_orders(load_fixture())
        assert_idempotent(ctx.sink, stages.emit_report_stage, ctx)
        assert_idempotent(ctx.sink, stages.emit_notify_stage, ctx)

    def test_conflicting_idempotency_key_raises(self):
        sink = EffectSink()
        sink.apply("k", "v1")
        self.assertFalse(sink.apply("k", "v1"))
        with self.assertRaises(AssertionError):
            sink.apply("k", "v2")


class TestDifferential(unittest.TestCase):
    """对拍：同一输入下，重构后与遗留实现的最终输出逐字节一致。"""

    def test_matches_legacy_on_fixture(self):
        lines = load_fixture()
        legacy_report, legacy_outbox = process_orders(lines)
        ctx = run_orders(lines)
        self.assertEqual(render_report(ctx.sink), legacy_report)
        self.assertEqual(render_outbox(ctx.sink), legacy_outbox)

    def test_matches_legacy_on_edge_cases(self):
        cases = [
            [],
            ["\n", "  \n"],
            ["X1,a,1,USD"],
            ["X1,a,1,usd\nX1,b,2,USD\nX2,c,0,CNY\nX3,,5,EUR\nX4,d,zz,CNY"],
            ["X5,e,0.001,USD\nX6,f,999999.99,EUR\nX7,g,1,JPY"],
            ["only,three,cols"],
            [",,,"],
        ]
        for lines in cases:
            with self.subTest(lines=lines):
                legacy_report, legacy_outbox = process_orders(lines)
                ctx = run_orders(lines)
                self.assertEqual(render_report(ctx.sink), legacy_report)
                self.assertEqual(render_outbox(ctx.sink), legacy_outbox)

    def test_matches_golden_files(self):
        ctx = run_orders(load_fixture())
        with open(os.path.join(DATA_DIR, "expected_report.txt"),
                  encoding="utf-8") as fh:
            self.assertEqual(render_report(ctx.sink), fh.read())
        with open(os.path.join(DATA_DIR, "expected_outbox.log"),
                  encoding="utf-8") as fh:
            self.assertEqual(render_outbox(ctx.sink), fh.read())


if __name__ == "__main__":
    unittest.main()
