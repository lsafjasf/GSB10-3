"""failure_cluster 自测：python3 -m unittest test_failure_cluster -v"""
import unittest

from failure_cluster import (
    Failure, cluster_failures, normalize_message, locate_failure,
    critical_reasons, load_failures,
)

STACK_A = (
    'Traceback (most recent call last):\n'
    '  File "/usr/lib/python3.12/unittest/case.py", line 589, in _callTestMethod\n'
    '    testMethod()\n'
    '  File "tests/test_x.py", line 10, in runTest\n'
    '    f()\n'
    '  File "src/svc.py", line 87, in parse\n'
    '    return int(raw)\n'
    "ValueError: invalid literal for int() with base 10: 'abc'"
)


def mk(name, etype="ValueError", msg="invalid literal for int() with base 10: 'abc'",
       stack=STACK_A, **kw):
    return Failure(test_name=name, error_type=etype, message=msg, stack=stack, **kw)


class TestNormalize(unittest.TestCase):
    def test_noise_masked(self):
        a = normalize_message("request timed out after 5000 ms")
        b = normalize_message("request timed out after 4877 ms")
        self.assertEqual(a, b)
        self.assertIn("<N>", a)

    def test_addr_uuid_path_quoted(self):
        s = normalize_message(
            "obj 0x7f3a21 id 123e4567-e89b-12d3-a456-426614174000 "
            "at /home/u/app/x.py said 'boom'")
        self.assertIn("<ADDR>", s)
        self.assertIn("<UUID>", s)
        self.assertIn("<PATH>", s)
        self.assertIn("'<V>'", s)

    def test_identical_messages_same_template(self):
        self.assertEqual(normalize_message("config file not found: app.yaml"),
                         normalize_message("config file not found: app.yaml"))


class TestLocate(unittest.TestCase):
    def test_deepest_business_frame(self):
        f, fn = locate_failure(STACK_A)
        self.assertEqual((f, fn), ("src/svc.py", "parse"))

    def test_framework_only_fallback(self):
        """全是框架帧时回退到最后一帧，而不是返回 unknown"""
        stack = ('  File "/usr/lib/python3.12/unittest/case.py", line 1, in run\n'
                 '    x()\n')
        f, fn = locate_failure(stack)
        self.assertEqual((f, fn), ("/usr/lib/python3.12/unittest/case.py", "run"))

    def test_empty_stack(self):
        self.assertEqual(locate_failure(""), ("<unknown>", "<unknown>"))

    def test_js_style(self):
        stack = "Error: boom\n    at send (/app/src/http.js:133:11)\n    at run (/app/t.js:5:3)"
        self.assertEqual(locate_failure(stack), ("/app/src/http.js", "send"))


class TestClustering(unittest.TestCase):
    def test_single_root_cause(self):
        """单一根因：几十条失败、消息细节不同 -> 1 个簇"""
        fs = [mk("t%02d" % i,
                 msg="invalid literal for int() with base 10: 'v%d'" % i)
              for i in range(30)]
        clusters = cluster_failures(fs)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].size, 30)
        self.assertEqual(clusters[0].representative.test_name, "t00")

    def test_multiple_independent_root_causes(self):
        """多个独立根因 -> 各自成簇"""
        fs = ([mk("a%d" % i) for i in range(5)]
              + [mk("b%d" % i, etype="KeyError", msg="'user_id'",
                    stack='  File "src/store.py", line 1, in get\n    x\n') for i in range(3)]
              + [mk("c%d" % i, etype="TimeoutError",
                    msg="request timed out after %d ms" % (1000 + i),
                    stack='  File "src/http.py", line 2, in send\n    x\n') for i in range(2)])
        clusters = cluster_failures(fs)
        self.assertEqual(len(clusters), 3)
        sizes = sorted(c.size for c in clusters)
        self.assertEqual(sizes, [2, 3, 5])

    def test_single_failure(self):
        """只有一条失败 -> 恰好 1 个簇、大小 1"""
        clusters = cluster_failures([mk("only_one")])
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].size, 1)
        self.assertFalse(clusters[0].isolated)

    def test_empty_input(self):
        self.assertEqual(cluster_failures([]), [])

    def test_identical_failures(self):
        """失败信息完全相同 -> 全部并入 1 个簇"""
        fs = [mk("dup%d" % i, msg="config file not found: app.yaml") for i in range(4)]
        clusters = cluster_failures(fs)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].size, 4)

    def test_critical_isolated_from_noise(self):
        """关键失败即使特征与普通失败完全一致，也必须单独成簇"""
        noise = [mk("noise%d" % i) for i in range(10)]
        critical = mk("test_auth_token_expiry_parse")  # 命中 R3 关键模式 auth
        clusters = cluster_failures(noise + [critical])
        self.assertEqual(len(clusters), 2)
        iso = [c for c in clusters if c.isolated]
        self.assertEqual(len(iso), 1)
        self.assertEqual(iso[0].size, 1)
        self.assertEqual(iso[0].representative.test_name,
                         "test_auth_token_expiry_parse")
        self.assertTrue(any("R3" in r for r in iso[0].reasons))
        big = [c for c in clusters if not c.isolated][0]
        self.assertEqual(big.size, 10)

    def test_critical_rules(self):
        by_type = mk("t1", etype="SecurityError", msg="x")
        by_flag = mk("t2", critical=True)
        by_tag = mk("t3", tags=["payment"])
        plain = mk("t4")
        self.assertTrue(any("R2" in r for r in critical_reasons(by_type)))
        self.assertTrue(any("R1" in r for r in critical_reasons(by_flag)))
        self.assertTrue(any("R3" in r for r in critical_reasons(by_tag)))
        self.assertEqual(critical_reasons(plain), [])

    def test_representative_is_stable_and_typical(self):
        fs = [mk("z1", msg="invalid literal for int() with base 10: 'rare'")]
        fs += [mk("a%d" % i, msg="invalid literal for int() with base 10: 'abc'")
               for i in range(3)]
        rep = cluster_failures(fs)[0].representative
        self.assertEqual(rep.message, "invalid literal for int() with base 10: 'abc'")
        self.assertEqual(rep.test_name, "a0")


class TestSampleData(unittest.TestCase):
    def test_sample_file_end_to_end(self):
        failures = load_failures("sample_failures.json")
        clusters = cluster_failures(failures)
        self.assertEqual(sum(c.size for c in clusters), len(failures))
        isolated = [c for c in clusters if c.isolated]
        self.assertEqual(len(isolated), 3)  # SecurityError / DataCorruption / auth-ValueError
        biggest = max((c for c in clusters if not c.isolated), key=lambda c: c.size)
        self.assertEqual(biggest.size, 22)  # 主根因 ValueError 簇
        self.assertEqual(biggest.location_file, "src/cart_service.py")
        self.assertEqual(biggest.location_func, "parse_quantity")


if __name__ == "__main__":
    unittest.main()
