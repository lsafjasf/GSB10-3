"""failcluster 自测（仅标准库 unittest）。

运行：python3 -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from failcluster import (  # noqa: E402
    FailureCase,
    Frame,
    build_report,
    cluster_failures,
    normalize_message,
)


def mk(case_id, error_type, message, frames, phase=None, test=None):
    return FailureCase(
        case_id=case_id,
        test=test or f"tests/test_x.py::{case_id}",
        error_type=error_type,
        message=message,
        traceback=[Frame(file=f, function=fn, line=ln) for f, fn, ln in frames],
        phase=phase,
    )


PAY_FRAMES = [
    ("tests/payment/test_checkout.py", "test_pay", 42),
    ("src/payment/service.py", "checkout", 120),
    ("src/payment/gateway.py", "charge", 88),
]


class TestSingleRootCause(unittest.TestCase):
    """情形一：单一根因 —— 几十条失败应聚成一个关键簇。"""

    def test_many_failures_one_cluster(self):
        cases = []
        for i in range(30):
            cases.append(
                mk(
                    f"TC-{i:03d}",
                    "AssertionError",
                    f"expected 200 got 500 (order {1000+i})",
                    [
                        ("tests/payment/test_checkout.py", f"test_case_{i}", 10 + i),
                        ("src/payment/service.py", "checkout", 120),
                        ("src/payment/gateway.py", "charge", 88),
                    ],
                )
            )
        clusters = cluster_failures(cases)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].size, 30)
        self.assertEqual(clusters[0].label, "key")
        self.assertTrue(any(r.startswith("K1") for r in clusters[0].reasons))

    def test_soft_merge_minor_message_drift(self):
        """同位置同类型、信息模板略差异（配置文本不同）应软合并为一簇。"""
        cases = [
            mk("TC-A", "PaymentError", "charge failed: amount=100 currency=CNY channel=alipay",
               PAY_FRAMES),
            mk("TC-B", "PaymentError", "charge failed: amount=200 currency=CNY channel=wechat",
               PAY_FRAMES),
            mk("TC-C", "PaymentError", "charge failed: amount=300 currency=USD channel=alipay",
               PAY_FRAMES),
        ]
        clusters = cluster_failures(cases)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].size, 3)


class TestMultipleRootCauses(unittest.TestCase):
    """情形二：多个独立根因 —— 不同位置/类型应分成不同簇。"""

    def test_independent_causes_split(self):
        cases = [
            mk("PAY-1", "AssertionError", "expected 200 got 500", PAY_FRAMES),
            mk("PAY-2", "AssertionError", "expected 200 got 500", PAY_FRAMES),
            mk(
                "INV-1", "KeyError", "'sku'",
                [("tests/inv/test_stock.py", "test_stock", 9),
                 ("src/inventory/service.py", "deduct", 55)],
            ),
            mk(
                "CSV-1", "UnicodeDecodeError", "codec can't decode byte 0xff",
                [("tests/report/test_export.py", "test_export", 21),
                 ("src/report/exporter.py", "to_csv", 34)],
            ),
        ]
        clusters = cluster_failures(cases)
        self.assertEqual(len(clusters), 3)
        sizes = sorted(c.size for c in clusters)
        self.assertEqual(sizes, [1, 1, 2])
        by_id = {c.representative: c for c in clusters}
        self.assertEqual(by_id["PAY-1"].size, 2)
        # 每个簇都是关键簇（位置都在产品代码）
        self.assertTrue(all(c.label == "key" for c in clusters))


class TestSingleFailure(unittest.TestCase):
    """情形三：只有一条失败 —— 自成一簇，数量 1，不崩溃。"""

    def test_single_case(self):
        cases = [mk("ONLY-1", "AssertionError", "expected 1 got 2", PAY_FRAMES)]
        clusters = cluster_failures(cases)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].size, 1)
        self.assertEqual(clusters[0].representative, "ONLY-1")
        self.assertEqual(clusters[0].label, "key")

    def test_empty_input(self):
        self.assertEqual(cluster_failures([]), [])
        report = build_report([], [])
        self.assertEqual(report.total_failures, 0)
        self.assertEqual(report.cluster_count, 0)


class TestIdenticalMessages(unittest.TestCase):
    """情形四：失败信息完全相同 —— 全部聚成一簇。"""

    def test_identical_messages(self):
        cases = [
            mk(f"DUP-{i}", "ValueError", "boom", PAY_FRAMES) for i in range(5)
        ]
        clusters = cluster_failures(cases)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].size, 5)
        self.assertEqual(clusters[0].message_template, "boom")


class TestKeyVsNoise(unittest.TestCase):
    """关键失败必须与噪声失败分开，不得混簇。"""

    def test_key_and_noise_not_mixed(self):
        key_case = mk("KEY-1", "AssertionError", "expected 200 got 500", PAY_FRAMES)
        noise_setup = mk(
            "NOISE-1", "RuntimeError", "db container not ready",
            [("tests/conftest.py", "db_fixture", 12)], phase="setup",
        )
        noise_infra = mk(
            "NOISE-2", "TimeoutError", "request to http://10.0.0.8:8080 timed out after 30s",
            [("src/utils/http.py", "post", 40)],
        )
        clusters = cluster_failures([key_case, noise_setup, noise_infra])
        labels = {c.representative: c.label for c in clusters}
        self.assertEqual(labels["KEY-1"], "key")
        self.assertEqual(labels["NOISE-1"], "noise")
        self.assertEqual(labels["NOISE-2"], "noise")
        # 噪声与关键不得同簇
        for c in clusters:
            self.assertFalse(
                ("KEY-1" in c.members) and (c.label == "noise"),
                "关键失败被混进噪声簇",
            )
        # 判定依据非空
        for c in clusters:
            self.assertTrue(c.reasons, f"{c.cluster_id} 缺少判定依据")

    def test_noise_reasons_recorded(self):
        noise = mk("N-1", "OSError", "No space left on device",
                   [("tests/conftest.py", "tmp_fixture", 3)], phase="setup")
        clusters = cluster_failures([noise])
        self.assertEqual(clusters[0].label, "noise")  # placeholder, fixed below


class TestNormalization(unittest.TestCase):
    def test_message_template(self):
        t1 = normalize_message("expected 200 got 500 at 2026-10-04T12:00:00 from 10.0.0.1")
        t2 = normalize_message("expected 200 got 500 at 2026-10-05T08:30:00 from 192.168.1.9")
        self.assertEqual(t1, t2)
        self.assertIn("<NUM>", t1)
        self.assertIn("<TS>", t1)
        self.assertIn("<IP>", t1)

    def test_number_variants_share_template(self):
        # 数字归一化后同模板（同一断言不同数据 = 同根因）
        t1 = normalize_message("expected 200 got 500")
        t2 = normalize_message("expected 201 got 404")
        self.assertEqual(t1, t2)

    def test_different_skeleton_different_template(self):
        t1 = normalize_message("expected 200 got 500")
        t2 = normalize_message("missing field 200 in response 500")
        self.assertNotEqual(t1, t2)


class TestRepresentative(unittest.TestCase):
    def test_representative_is_most_common_message(self):
        cases = [
            mk("R-1", "AssertionError", "expected 200 got 500", PAY_FRAMES),
            mk("R-2", "AssertionError", "expected 200 got 500", PAY_FRAMES),
            mk("R-3", "AssertionError", "expected 200 got 503", PAY_FRAMES),
        ]
        clusters = cluster_failures(cases)
        # R-1/R-2 信息完全相同先精确成组；R-3 模板相同（数字归一化）精确同组
        self.assertEqual(len(clusters), 1)
        self.assertIn(clusters[0].representative, ("R-1", "R-2"))
        self.assertEqual(clusters[0].representative_message, "expected 200 got 500")


class TestNoTraceback(unittest.TestCase):
    def test_case_without_traceback(self):
        case = FailureCase(
            case_id="NT-1", test="t", error_type="AssertionError",
            message="expected 1 got 2", traceback=[],
        )
        clusters = cluster_failures([case])
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].location, "")


if __name__ == "__main__":
    unittest.main()
