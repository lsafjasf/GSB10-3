"""自测：验证四类检查在已知样本上的判定符合预期，并覆盖边界用例。

运行：python3 -m rng_quality.selftest   （或 python3 -m unittest）

自测数据全部来自 rng_quality.samples 的确定性生成器与代码内字面量，
不使用任何随机源，也不依赖任何待测 RNG 实现。
"""

import hashlib
import unittest

from .core import (
    MIN_BYTES,
    assess,
    bit_balance_test,
    periodicity_test,
    runs_test,
    uniformity_test,
)
from .samples import SAMPLE_BYTES, make_samples

# 固定样本集的 SHA-256 摘要：样本内容若被意外改动，自测立即失败，
# 保证“检查自身使用固定数据”这一性质可被验证。
SAMPLE_DIGESTS = {
    "good_sha256_counter": "c38e88a193896a289b0d22a1c6e89658cb1cc5be759b8b6c8c6723377242fe7b",
    "low_nibble_fixed": "ae0f212d1cb0fb7c467da54dcb61198c1372678ce07afe37899fb4875d4e1d32",
    "period_2bytes": "3d558540e8c59a9e6915aaa700c8d714c8927167920bf0bcf84c4ece2589f368",
    "period_16bytes": "44b7e4f7d8835dd354a15de1c9ad3c58b7f87d2fc436813a57d599d2b1fdf2de",
    "all_zeros": "4fe7b59af6de3b665b67788cc2f99892ab827efae3a467342b3bb4e3bc8e5bfe",
}

# 各样本在四项检查上的预期状态（顺序：uniformity, bit_balance, runs, periodicity）。
EXPECTED_STATUSES = {
    "good_sha256_counter": ("PASS", "PASS", "PASS", "PASS"),
    "low_nibble_fixed": ("FAIL", "FAIL", "FAIL", "FAIL"),
    "period_2bytes": ("FAIL", "PASS", "FAIL", "FAIL"),
    "period_16bytes": ("FAIL", "PASS", "PASS", "FAIL"),
    "all_zeros": ("FAIL", "FAIL", "FAIL", "FAIL"),
}

CHECK_ORDER = ("uniformity", "bit_balance", "runs", "periodicity")


class TestFixedSamples(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = make_samples(SAMPLE_BYTES)
        cls.reports = {name: assess(data) for name, data in cls.samples.items()}

    def test_samples_are_fixed(self):
        for name, data in self.samples.items():
            self.assertEqual(len(data), SAMPLE_BYTES, name)
            self.assertEqual(
                hashlib.sha256(data).hexdigest(), SAMPLE_DIGESTS[name],
                "样本 %s 内容发生变化，自测数据不再是固定数据" % name,
            )

    def test_expected_statuses(self):
        for name, report in self.reports.items():
            actual = tuple(
                next(r["status"] for r in report["results"] if r["check"] == check)
                for check in CHECK_ORDER
            )
            self.assertEqual(
                actual, EXPECTED_STATUSES[name],
                "样本 %s 判定不符：期望 %s，实际 %s" % (name, EXPECTED_STATUSES[name], actual),
            )

    def test_overall(self):
        self.assertEqual(self.reports["good_sha256_counter"]["overall"], "PASS")
        for name in ("low_nibble_fixed", "period_2bytes", "period_16bytes", "all_zeros"):
            self.assertEqual(self.reports[name]["overall"], "FAIL", name)

    def test_periodicity_finds_known_periods(self):
        # 字节级候选周期应命中构造的真实周期（参考信息）。
        for name, lag in (("period_2bytes", 2), ("period_16bytes", 16)):
            result = next(
                r for r in self.reports[name]["results"] if r["check"] == "periodicity"
            )
            self.assertEqual(result["stats"]["candidate_byte_lag"], lag, name)


class TestEdgeCases(unittest.TestCase):
    def test_empty_input(self):
        report = assess(b"")
        self.assertEqual(report["overall"], "SKIP")
        self.assertTrue(all(r["status"] == "SKIP" for r in report["results"]))

    def test_below_min_bytes_skips(self):
        # 恰好在阈值之下：即使是全零也不应误判为 FAIL，而是 SKIP。
        report = assess(bytes(MIN_BYTES - 1))
        self.assertEqual(report["overall"], "SKIP")
        self.assertTrue(all(r["status"] == "SKIP" for r in report["results"]))

    def test_at_min_bytes_runs(self):
        # 达到阈值：全零必须 FAIL。
        report = assess(bytes(MIN_BYTES))
        self.assertEqual(report["overall"], "FAIL")
        self.assertTrue(all(r["status"] == "FAIL" for r in report["results"]))

    def test_constant_byte_0xff(self):
        report = assess(b"\xff" * MIN_BYTES)
        self.assertEqual(report["overall"], "FAIL")
        self.assertTrue(all(r["status"] == "FAIL" for r in report["results"]))

    def test_accepts_bytearray(self):
        report = assess(bytearray(bytes(MIN_BYTES)))
        self.assertEqual(report["overall"], "FAIL")

    def test_deterministic_reports(self):
        data = make_samples(SAMPLE_BYTES)["low_nibble_fixed"]
        first = assess(data)
        second = assess(data)
        self.assertEqual(
            [r["status"] for r in first["results"]],
            [r["status"] for r in second["results"]],
        )
        self.assertEqual(
            [r["detail"] for r in first["results"]],
            [r["detail"] for r in second["results"]],
        )

    def test_single_check_functions(self):
        good = make_samples(SAMPLE_BYTES)["good_sha256_counter"]
        self.assertEqual(uniformity_test(good)["status"], "PASS")
        self.assertEqual(bit_balance_test(good)["status"], "PASS")
        self.assertEqual(runs_test(good)["status"], "PASS")
        self.assertEqual(periodicity_test(good)["status"], "PASS")
        bad = bytes(SAMPLE_BYTES)
        self.assertEqual(uniformity_test(bad)["status"], "FAIL")
        self.assertEqual(bit_balance_test(bad)["status"], "FAIL")
        self.assertEqual(runs_test(bad)["status"], "FAIL")
        self.assertEqual(periodicity_test(bad)["status"], "FAIL")


def main():
    suite = unittest.defaultTestLoader.loadTestsFromModule(__import__(__name__))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
