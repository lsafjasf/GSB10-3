"""Differential golden-master tests: legacy/ vs app/ must behave identically.

Every scenario runs the same inputs through both packages and compares the
full observable outputs. Both packages are exercised through identical
call shapes; the only allowed difference is where the thresholds come from.
"""

import unittest

import app.aggregator
import app.alerting
import app.api
import app.cache
import app.client
import app.pipeline
import app.report
import app.worker
import legacy.aggregator
import legacy.alerting
import legacy.api
import legacy.cache
import legacy.client
import legacy.pipeline
import legacy.report
import legacy.worker

from tests.fixtures import (
    always_failing_fetcher,
    fake_clock,
    flaky_fetcher,
    ok_fetcher,
    scripted_elapsed,
)



def build_fetcher(spec, exc_cls):
    kind = spec[0]
    if kind == "ok":
        value = spec[1] if len(spec) > 1 else "v"
        return ok_fetcher(value)
    if kind == "flaky":
        failures = spec[1]
        value = spec[2] if len(spec) > 2 else "v"
        return flaky_fetcher(failures, exc_cls, value)
    if kind == "fail":
        return always_failing_fetcher(exc_cls)
    raise AssertionError("unknown fetcher spec %r" % (spec,))


def client_scenarios():
    return [
        ("ok-first-try", ("ok", "a"), [0.0]),
        ("ok-after-two-failures", ("flaky", 2, "b"), [0.0, 1.0, 2.0]),
        ("retries-exhausted", ("fail",), [0.0, 1.0, 2.0, 3.0]),
        ("timeout-before-first-try", ("ok",), [30.0]),
        ("timeout-at-boundary", ("flaky", 1), [29.999, 30.0]),
        ("timeout-just-under-boundary", ("flaky", 1, "c"), [29.999, 29.999]),
    ]


def aggregator_scenarios():
    dense = [(float(i), 1.0) for i in range(250)]
    sparse = [(float(i * 30), 2.0) for i in range(5)]
    boundary = [(0.0, 1.0), (59.999, 1.0), (60.0, 1.0), (119.999, 1.0), (120.0, 1.0)]
    exact_batch = [(float(i), 0.5) for i in range(100)]
    over_batch = [(float(i), 0.5) for i in range(101)]
    return [
        ("empty", []),
        ("single", [(7.0, 3.5)]),
        ("dense-250", dense),
        ("sparse-30s-gap", sparse),
        ("interval-boundary", boundary),
        ("exact-batch-100", exact_batch),
        ("batch-101", over_batch),
    ]


def cache_scenarios():
    return [
        ("default-ttl-hit", None, 100.0, 200.0),
        ("default-ttl-expired", None, 100.0, 400.0),
        ("default-ttl-just-alive", None, 100.0, 399.999),
        ("explicit-ttl-hit", 120, 0.0, 119.999),
        ("explicit-ttl-expired", 120, 0.0, 120.0),
    ]


def alerting_scenarios():
    return [
        ("below", 0.949999),
        ("exact", 0.95),
        ("above", 1.0),
        ("zero", 0.0),
    ]


def cooldown_scenarios():
    return [
        ("never-alerted", None, 1000.0),
        ("cooldown-active", 0.0, 59.999),
        ("cooldown-expired-exact", 0.0, 60.0),
        ("cooldown-long-past", 0.0, 10_000.0),
    ]


def report_scenarios():
    return [
        ("ok", 0.4),
        ("warn-boundary", 0.475),
        ("warn", 0.5),
        ("critical-boundary", 0.95),
        ("critical", 0.99),
    ]


def worker_scenarios():
    return [
        ("empty", 0),
        ("one", 1),
        ("exact-batch", 100),
        ("batch-plus-one", 101),
        ("two-exact-batches", 200),
        ("two-and-half", 250),
    ]


def api_scenarios():
    return [
        ("rejected", ("ok",), [30.0]),
        ("ok-caches", ("ok", "x"), [0.0]),
        ("failed-no-cache", ("fail",), [0.0, 1.0, 2.0, 3.0]),
    ]


def pipeline_scenarios():
    dense = [(float(i), 1.0) for i in range(250)]
    sparse = [(float(i * 30), 2.0) for i in range(5)]
    return [
        (
            "normal",
            dict(
                samples=dense,
                ratios=[0.5, 0.95, 0.949, 1.0],
                fetcher=("flaky", 1, "v"),
                elapsed=[0.0, 1.0],
                last_alert_at=None,
                now=1000.0,
            ),
        ),
        (
            "all-fetches-fail",
            dict(
                samples=sparse,
                ratios=[0.1],
                fetcher=("fail",),
                elapsed=[0.0, 1.0, 2.0, 3.0],
                last_alert_at=900.0,
                now=1000.0,
            ),
        ),
        (
            "timeout",
            dict(
                samples=[],
                ratios=[0.95],
                fetcher=("ok",),
                elapsed=[30.0],
                last_alert_at=0.0,
                now=59.999,
            ),
        ),
        (
            "cooldown-blocks-alert",
            dict(
                samples=dense[:100],
                ratios=[0.99, 0.95],
                fetcher=("ok", "z"),
                elapsed=[0.0],
                last_alert_at=10.0,
                now=69.999,
            ),
        ),
        (
            "empty",
            dict(
                samples=[],
                ratios=[],
                fetcher=("ok", "e"),
                elapsed=[0.0],
                last_alert_at=None,
                now=0.0,
            ),
        ),
    ]


class ClientDiffTest(unittest.TestCase):
    def test_fetch_with_retry_matches_legacy(self):
        for name, fetcher_spec, elapsed_script in client_scenarios():
            with self.subTest(scenario=name):
                before = legacy.client.fetch_with_retry(
                    build_fetcher(fetcher_spec, legacy.client.TransientError),
                    scripted_elapsed(elapsed_script),
                )
                after = app.client.fetch_with_retry(
                    build_fetcher(fetcher_spec, app.client.TransientError),
                    scripted_elapsed(elapsed_script),
                )
                self.assertEqual(before, after)


class AggregatorDiffTest(unittest.TestCase):
    def test_aggregate_matches_legacy(self):
        for name, samples in aggregator_scenarios():
            with self.subTest(scenario=name):
                self.assertEqual(
                    legacy.aggregator.aggregate(samples),
                    app.aggregator.aggregate(samples),
                )


class CacheDiffTest(unittest.TestCase):
    def test_cache_matches_legacy(self):
        for name, ttl, put_at, get_at in cache_scenarios():
            with self.subTest(scenario=name):
                outputs = []
                for module in (legacy.cache, app.cache):
                    clock = fake_clock(put_at)
                    store = module.Cache(clock)
                    store.put("k", "v", ttl=ttl)
                    clock.set(get_at)
                    outputs.append((store.get("k"), len(store)))
                self.assertEqual(outputs[0], outputs[1])


class AlertingDiffTest(unittest.TestCase):
    def test_breached_matches_legacy(self):
        for name, ratio in alerting_scenarios():
            with self.subTest(scenario=name):
                self.assertEqual(
                    legacy.alerting.breached(ratio), app.alerting.breached(ratio)
                )

    def test_may_alert_matches_legacy(self):
        for name, last, now in cooldown_scenarios():
            with self.subTest(scenario=name):
                self.assertEqual(
                    legacy.alerting.may_alert(last, now),
                    app.alerting.may_alert(last, now),
                )


class ReportDiffTest(unittest.TestCase):
    def test_grade_matches_legacy(self):
        for name, ratio in report_scenarios():
            with self.subTest(scenario=name):
                self.assertEqual(legacy.report.grade(ratio), app.report.grade(ratio))

    def test_format_timeout_budget_matches_legacy(self):
        self.assertEqual(
            legacy.report.format_timeout_budget(), app.report.format_timeout_budget()
        )


class WorkerDiffTest(unittest.TestCase):
    def test_drain_matches_legacy(self):
        for name, count in worker_scenarios():
            with self.subTest(scenario=name):
                outputs = []
                for module in (legacy.worker, app.worker):
                    batches = []
                    flushes = module.drain(list(range(count)), batches.append)
                    outputs.append((flushes, batches))
                self.assertEqual(outputs[0], outputs[1])


class ApiDiffTest(unittest.TestCase):
    def test_handle_matches_legacy(self):
        for name, fetcher_spec, elapsed_script in api_scenarios():
            with self.subTest(scenario=name):
                outputs = []
                for client_module, cache_module, api_module in (
                    (legacy.client, legacy.cache, legacy.api),
                    (app.client, app.cache, app.api),
                ):
                    clock = fake_clock(0.0)
                    store = cache_module.Cache(clock)
                    result = api_module.handle(
                        build_fetcher(fetcher_spec, client_module.TransientError),
                        scripted_elapsed(elapsed_script),
                        store,
                    )
                    outputs.append((result, store.get("last_value"), len(store)))
                self.assertEqual(outputs[0], outputs[1])


class PipelineDiffTest(unittest.TestCase):
    def test_run_pipeline_matches_legacy(self):
        for name, kwargs in pipeline_scenarios():
            with self.subTest(scenario=name):
                outputs = []
                for client_module, pipeline_module in (
                    (legacy.client, legacy.pipeline),
                    (app.client, app.pipeline),
                ):
                    run_kwargs = dict(kwargs)
                    run_kwargs["fetcher"] = build_fetcher(
                        kwargs["fetcher"], client_module.TransientError
                    )
                    run_kwargs["elapsed"] = scripted_elapsed(kwargs["elapsed"])
                    outputs.append(
                        pipeline_module.run_pipeline(
                            clock=fake_clock(0.0), **run_kwargs
                        )
                    )
                self.assertEqual(outputs[0], outputs[1])


if __name__ == "__main__":
    unittest.main()
