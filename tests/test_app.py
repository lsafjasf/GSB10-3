"""Regression tests for the refactored order module (stdlib unittest only)."""

import threading
import unittest
import warnings

import app


def make_items():
    return [("apple", 3.0), ("bread", 2.0), ("milk", 5.0)]


class SingleRequestTest(unittest.TestCase):
    def test_single_request_result_and_audit(self):
        ctx = app.RequestContext(request_id="r-1", user_id="alice", discount=0.1)
        result = app.handle_request(ctx, make_items())
        self.assertEqual(result, {"request_id": "r-1", "user_id": "alice", "total": 9.0})
        messages = [entry["message"] for entry in ctx.audit_log]
        self.assertEqual(
            messages,
            ["request started", "added apple", "added bread", "added milk", "request finished"],
        )
        self.assertTrue(all(entry["request_id"] == "r-1" for entry in ctx.audit_log))

    def test_default_discount_is_zero(self):
        ctx = app.RequestContext(request_id="r-2", user_id="bob")
        result = app.handle_request(ctx, make_items())
        self.assertEqual(result["total"], 10.0)


class ConcurrentRequestsTest(unittest.TestCase):
    def test_concurrent_requests_do_not_pollute_each_other(self):
        worker_count = 16
        barrier = threading.Barrier(worker_count)
        results = {}
        errors = []
        lock = threading.Lock()

        def worker(index):
            ctx = app.RequestContext(
                request_id="req-%d" % index,
                user_id="user-%d" % index,
                discount=(index % 5) * 0.1,
            )
            try:
                barrier.wait(timeout=10)
                result = app.handle_request(ctx, make_items())
                expected_total = round(10.0 * (1 - ctx.discount), 2)
                with lock:
                    results[index] = (result, expected_total, ctx)
            except Exception as exc:  # pragma: no cover - failure path
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(worker_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(errors, [])
        self.assertEqual(len(results), worker_count)
        for index, (result, expected_total, ctx) in results.items():
            self.assertEqual(result["request_id"], "req-%d" % index)
            self.assertEqual(result["user_id"], "user-%d" % index)
            self.assertEqual(result["total"], expected_total)
            self.assertEqual(len(ctx.audit_log), 5)
            self.assertTrue(
                all(entry["request_id"] == "req-%d" % index for entry in ctx.audit_log),
                "audit entries leaked across requests",
            )


class MissingContextTest(unittest.TestCase):
    def test_handle_request_without_context_raises(self):
        with self.assertRaises(app.MissingContextError):
            app.handle_request(None, make_items())

    def test_add_item_without_context_raises(self):
        with self.assertRaises(app.MissingContextError):
            app.add_item(None, "apple", 3.0)

    def test_wrong_type_is_rejected(self):
        with self.assertRaises(app.MissingContextError):
            app.handle_request({"request_id": "r"}, make_items())


class LegacyInterfaceTest(unittest.TestCase):
    def test_legacy_shim_warns_and_still_works(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = app.handle_request_legacy("r-old", "carol", make_items(), discount=0.2)
        self.assertEqual(result["total"], 8.0)
        self.assertEqual(result["request_id"], "r-old")
        self.assertTrue(
            any(issubclass(w.category, DeprecationWarning) for w in caught),
            "expected a DeprecationWarning from the legacy shim",
        )

    def test_legacy_module_still_importable_for_reference(self):
        import legacy_app

        legacy_app._audit_log.clear()
        result = legacy_app.handle_request("r-legacy", "dave", make_items(), discount=0.5)
        self.assertEqual(result["total"], 5.0)
        legacy_app._audit_log.clear()


if __name__ == "__main__":
    unittest.main()
