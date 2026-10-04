"""回归测试：C1..C9 每条约定至少一个通过用例与一个被破坏用例。

被破坏用例 = 以违反约定的方式调用，必须抛出 ContractViolation。
另外覆盖取值范围边界（test_boundary_*）。
"""

import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

import pipeline
from pipeline import ContractViolation, Pipeline, validate_result


def make_job(job_id="j1", kind="email", payload=None, priority=5):
    return {"id": job_id, "kind": kind,
            "payload": payload if payload is not None else {}, "priority": priority}


def make_open_pipeline(**kwargs):
    p = Pipeline(**kwargs)
    p.open()
    return p


class TestC1JobShape(unittest.TestCase):
    def test_pass_valid_job(self):
        p = make_open_pipeline()
        p.submit(make_job())

    def test_pass_payload_allows_nested_content(self):
        p = make_open_pipeline()
        p.submit(make_job(payload={"to": "a@b", "n": 1}))

    def test_violation_missing_key(self):
        p = make_open_pipeline()
        bad = make_job()
        del bad["priority"]
        with self.assertRaises(ContractViolation) as ctx:
            p.submit(bad)
        self.assertIn("[C1]", str(ctx.exception))

    def test_violation_wrong_types(self):
        p = make_open_pipeline()
        for bad in (
            make_job(job_id=""),
            make_job(kind="sms"),
            make_job(payload=["not", "a", "dict"]),
            "not-a-dict",
        ):
            with self.assertRaises(ContractViolation) as ctx:
                p.submit(bad)
            self.assertIn("[C1]", str(ctx.exception))


class TestC2ResultShape(unittest.TestCase):
    def test_pass_run_results(self):
        p = make_open_pipeline()
        p.submit(make_job())
        results = p.run()
        self.assertEqual(len(results), 1)
        for result in results:
            validate_result(result)

    def test_violation_validate_result_rejects_bad_shapes(self):
        for bad in (
            {},
            {"job_id": "j1", "status": "ok", "attempts": 1, "extra": 1},
            {"job_id": "", "status": "ok", "attempts": 1},
            {"job_id": "j1", "status": "weird", "attempts": 1},
            {"job_id": "j1", "status": "ok", "attempts": 0},
            {"job_id": "j1", "status": "ok", "attempts": "1"},
            "not-a-dict",
        ):
            with self.assertRaises(ContractViolation) as ctx:
                validate_result(bad)
            self.assertIn("[C2]", str(ctx.exception))


class TestC3LifecycleOrder(unittest.TestCase):
    def test_pass_full_lifecycle(self):
        p = Pipeline()
        p.open()
        p.submit(make_job())
        p.run()
        p.close()

    def test_violation_submit_before_open(self):
        p = Pipeline()
        with self.assertRaises(ContractViolation) as ctx:
            p.submit(make_job())
        self.assertIn("[C3]", str(ctx.exception))

    def test_violation_run_before_open(self):
        with self.assertRaises(ContractViolation) as ctx:
            Pipeline().run()
        self.assertIn("[C3]", str(ctx.exception))

    def test_violation_open_twice(self):
        p = make_open_pipeline()
        with self.assertRaises(ContractViolation) as ctx:
            p.open()
        self.assertIn("[C3]", str(ctx.exception))

    def test_violation_submit_after_close(self):
        p = make_open_pipeline()
        p.submit(make_job())
        p.close()
        with self.assertRaises(ContractViolation) as ctx:
            p.submit(make_job("j2"))
        self.assertIn("[C3]", str(ctx.exception))

    def test_violation_close_twice(self):
        p = make_open_pipeline()
        p.close()
        with self.assertRaises(ContractViolation) as ctx:
            p.close()
        self.assertIn("[C3]", str(ctx.exception))


class TestC4RunNeedsJob(unittest.TestCase):
    def test_pass_run_after_submit(self):
        p = make_open_pipeline()
        p.submit(make_job())
        self.assertEqual(p.run()[0]["job_id"], "j1")

    def test_violation_empty_run(self):
        p = make_open_pipeline()
        with self.assertRaises(ContractViolation) as ctx:
            p.run()
        self.assertIn("[C4]", str(ctx.exception))


class TestC5PriorityRange(unittest.TestCase):
    def test_pass_priority_boundaries(self):
        for priority in (0, 9):
            p = make_open_pipeline()
            p.submit(make_job("j{0}".format(priority), priority=priority))

    def test_violation_priority_out_of_range(self):
        for priority in (-1, 10, 100):
            p = make_open_pipeline()
            with self.assertRaises(ContractViolation) as ctx:
                p.submit(make_job("j{0}".format(priority), priority=priority))
            self.assertIn("[C5]", str(ctx.exception))

    def test_violation_priority_wrong_type(self):
        for priority in (5.0, True, "5"):
            p = make_open_pipeline()
            with self.assertRaises(ContractViolation) as ctx:
                p.submit(make_job("j", priority=priority))
            self.assertIn("[C5]", str(ctx.exception))


class TestC6ConstructorRanges(unittest.TestCase):
    def test_pass_constructor_boundaries(self):
        Pipeline(max_retries=0, timeout=300.0)
        Pipeline(max_retries=5, timeout=0.0001)

    def test_violation_retries_out_of_range(self):
        for kwargs in ({"max_retries": -1}, {"max_retries": 6}):
            with self.assertRaises(ContractViolation) as ctx:
                Pipeline(**kwargs)
            self.assertIn("[C6]", str(ctx.exception))

    def test_violation_timeout_out_of_range(self):
        for kwargs in ({"timeout": 0}, {"timeout": -1.0}, {"timeout": 300.1}):
            with self.assertRaises(ContractViolation) as ctx:
                Pipeline(**kwargs)
            self.assertIn("[C6]", str(ctx.exception))

    def test_violation_constructor_wrong_types(self):
        for kwargs in ({"max_retries": True}, {"timeout": True},
                       {"max_retries": "3"}, {"timeout": "30"}):
            with self.assertRaises(ContractViolation) as ctx:
                Pipeline(**kwargs)
            self.assertIn("[C6]", str(ctx.exception))


class TestC7UniqueIds(unittest.TestCase):
    def test_pass_distinct_ids(self):
        p = make_open_pipeline()
        p.submit(make_job("j1"))
        p.submit(make_job("j2"))

    def test_violation_duplicate_id(self):
        p = make_open_pipeline()
        p.submit(make_job("j1"))
        with self.assertRaises(ContractViolation) as ctx:
            p.submit(make_job("j1"))
        self.assertIn("[C7]", str(ctx.exception))


class TestC8ThreadAffinity(unittest.TestCase):
    def test_pass_used_entirely_in_worker_thread(self):
        errors = []

        def worker():
            try:
                p = Pipeline()
                p.open()
                p.submit(make_job())
                p.run()
                p.close()
            except Exception as exc:  # noqa: BLE001 - 记录跨线程错误
                errors.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(errors, [])

    def test_violation_submit_from_other_thread(self):
        p = make_open_pipeline()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(p.submit, make_job())
            with self.assertRaises(ContractViolation) as ctx:
                future.result(timeout=5)
        self.assertIn("[C8]", str(ctx.exception))

    def test_violation_close_from_other_thread(self):
        p = make_open_pipeline()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(p.close)
            with self.assertRaises(ContractViolation) as ctx:
                future.result(timeout=5)
        self.assertIn("[C8]", str(ctx.exception))


class TestC9NoConcurrentRun(unittest.TestCase):
    def test_pass_sequential_runs(self):
        p = make_open_pipeline()
        p.submit(make_job("j1"))
        p.run()
        p.submit(make_job("j2"))
        p.run()

    def test_violation_reentrant_run_from_callback(self):
        p = make_open_pipeline()
        p.submit(make_job())

        def reentrant(_job):
            p.run()

        with self.assertRaises(ContractViolation) as ctx:
            p.run(on_job=reentrant)
        self.assertIn("[C9]", str(ctx.exception))

    def test_violation_run_flag_still_cleared_after_error(self):
        p = make_open_pipeline()
        p.submit(make_job())
        with self.assertRaises(ContractViolation):
            p.run(on_job=lambda _job: p.run())
        # 恢复后仍可正常 run（_running 标志在 finally 中复位）
        self.assertEqual(p.run()[0]["status"], "ok")


class TestBoundaries(unittest.TestCase):
    """边界用例汇总：闭区间两端通过，越界一律拒绝。"""

    def test_priority_inclusive_boundaries(self):
        p = make_open_pipeline()
        p.submit(make_job("lo", priority=0))
        p.submit(make_job("hi", priority=9))
        self.assertEqual(len(p.run()), 2)

    def test_retries_inclusive_boundaries(self):
        self.assertEqual(make_open_pipeline(max_retries=0).max_retries, 0)
        self.assertEqual(make_open_pipeline(max_retries=5).max_retries, 5)

    def test_timeout_lower_bound_exclusive(self):
        with self.assertRaises(ContractViolation):
            Pipeline(timeout=0)
        Pipeline(timeout=0.000001)

    def test_timeout_upper_bound_inclusive(self):
        Pipeline(timeout=300)
        with self.assertRaises(ContractViolation):
            Pipeline(timeout=300.000001)

    def test_attempts_reflects_retries_boundaries(self):
        p0 = make_open_pipeline(max_retries=0)
        p0.submit(make_job())
        self.assertEqual(p0.run()[0]["attempts"], 1)
        p5 = make_open_pipeline(max_retries=5)
        p5.submit(make_job())
        self.assertEqual(p5.run()[0]["attempts"], 6)

    def test_empty_string_and_unknown_kind_rejected(self):
        p = make_open_pipeline()
        with self.assertRaises(ContractViolation):
            p.submit(make_job(job_id=""))
        with self.assertRaises(ContractViolation):
            p.submit(make_job(kind="EMAIL"))  # 大小写敏感


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)
