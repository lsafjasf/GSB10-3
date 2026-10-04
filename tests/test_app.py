"""回归测试：单请求 / 并发请求 / 上下文缺失 / 旧接口 / 全局状态残留扫描。

运行：python3 -m unittest -v
"""
import ast
import pathlib
import sys
import threading
import unittest

SRC_DIR = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import app  # noqa: E402
import legacy_app  # noqa: E402

APP_SRC = SRC_DIR / "app.py"


def _run_concurrent(handler, count=8):
    """count 个请求线程在同一屏障点交错执行，返回 (results, polluted)。"""
    barrier = threading.Barrier(count)
    results = [None] * count
    threads = []

    def worker(i):
        request = {"request_id": "req-%d" % i, "user": "user-%d" % i}
        if handler is app.handle_request:
            results[i] = handler(app.RequestContext(request), barrier)
        else:
            results[i] = handler(request, barrier)

    for i in range(count):
        threads.append(threading.Thread(target=worker, args=(i,)))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    polluted = [
        i
        for i, r in enumerate(results)
        if r["request_id"] != "req-%d" % i
        or r["user"] != "user-%d" % i
        or r["g_request_id"] != "req-%d" % i
    ]
    return results, polluted


class SingleRequestTest(unittest.TestCase):
    def test_single_request_ok(self):
        ctx = app.RequestContext({"request_id": "req-1", "user": "alice"})
        result = app.handle_request(ctx)
        self.assertEqual(result["request_id"], "req-1")
        self.assertEqual(result["user"], "alice")
        self.assertEqual(result["g_request_id"], "req-1")
        self.assertEqual(ctx.g["handler"], "handle_request")

    def test_context_isolation_by_construction(self):
        ctx1 = app.RequestContext({"request_id": "a", "user": "u1"})
        ctx2 = app.RequestContext({"request_id": "b", "user": "u2"})
        self.assertIsNot(ctx1.g, ctx2.g)
        self.assertEqual(app.handle_request(ctx1)["request_id"], "a")
        self.assertEqual(app.handle_request(ctx2)["request_id"], "b")
        self.assertEqual(ctx1.g["request_id"], "a")


class ConcurrentRequestTest(unittest.TestCase):
    def test_refactored_no_pollution(self):
        results, polluted = _run_concurrent(app.handle_request)
        self.assertEqual(len(results), 8)
        self.assertEqual(polluted, [], "重构版并发请求出现污染: %s" % polluted)

    def test_legacy_baseline_does_pollute(self):
        # 对比基线：旧版在相同交错条件下必须出现污染，证明测试有效
        _, polluted = _run_concurrent(legacy_app.handle_request)
        self.assertTrue(polluted, "旧版未复现污染，对比结论不成立")


class MissingContextTest(unittest.TestCase):
    def test_handle_with_none_ctx(self):
        with self.assertRaises(app.ContextMissingError):
            app.handle_request(None)

    def test_handle_with_wrong_type(self):
        with self.assertRaises(app.ContextMissingError):
            app.handle_request({"request_id": "x"})

    def test_context_with_none_request(self):
        with self.assertRaises(app.ContextMissingError):
            app.RequestContext(None)


class LegacyInterfaceTest(unittest.TestCase):
    def test_old_global_apis_raise(self):
        for fn_name, args in (
            ("set_request", ({"request_id": "x"},)),
            ("get_request", ()),
            ("current_user", ()),
            ("get_request_id", ()),
        ):
            with self.subTest(api=fn_name):
                with self.assertRaises(RuntimeError):
                    getattr(app, fn_name)(*args)

    def test_legacy_module_still_importable(self):
        # 旧模块保留用于回归对比，不应被误删
        self.assertTrue(hasattr(legacy_app, "handle_request"))


class NoResidualGlobalStateTest(unittest.TestCase):
    """AST 扫描重构后源码：禁止 global 语句与模块级可变容器。"""

    def test_no_global_statement(self):
        tree = ast.parse(APP_SRC.read_text(encoding="utf-8"))
        offenders = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Global)]
        self.assertEqual(offenders, [], "app.py 仍存在 global 语句: %s" % offenders)

    def test_no_mutable_module_level_state(self):
        tree = ast.parse(APP_SRC.read_text(encoding="utf-8"))
        mutable = (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp)
        bad_calls = {"dict", "list", "set"}
        offenders = []
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            value = node.value
            if isinstance(value, mutable):
                offenders.append(node.lineno)
            elif (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id in bad_calls
            ):
                offenders.append(node.lineno)
        self.assertEqual(offenders, [], "app.py 存在模块级可变状态: %s" % offenders)


if __name__ == "__main__":
    unittest.main(verbosity=2)
