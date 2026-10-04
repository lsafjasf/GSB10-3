"""边界层回归测试（仅标准库 unittest）。

覆盖：
- 合法输入：与第三方模块直接计算结果、与对拍数据三者一致；
- 字段缺失 / 类型错误：INVALID_INPUT，可读原因，不进入核心逻辑，不写文件；
- 内部抛异常：被隔离转换为 INTERNAL_ERROR，不向调用方抛出；
- 原子性：任何失败都不留半成品文件，已有文件不被破坏。
"""

import glob
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import boundary
import legacy_third_party as legacy

DATA_DIR = os.path.join(ROOT, "data", "differential")


def _load_cases():
    cases = []
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "case_*.json"))):
        with open(path, encoding="utf-8") as fh:
            cases.append((os.path.basename(path), json.load(fh)))
    return cases


class ValidInputTests(unittest.TestCase):
    def test_valid_input_succeeds_and_writes_file(self):
        record = {
            "id": "T-1",
            "currency": "CNY",
            "items": [{"name": "a", "qty": 1, "price": 10}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "report.json")
            result = boundary.process(record, out)

            self.assertTrue(result.ok)
            self.assertIsNone(result.error)
            self.assertEqual(result.value, legacy.compute_report(record))
            with open(out, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh), result.value)

    def test_differential_replay_matches_legacy_and_fixtures(self):
        # 对拍：重构层输出 == 第三方模块直接输出 == 对拍文件中的 expected_report
        for name, case in _load_cases():
            with self.subTest(case=name):
                with tempfile.TemporaryDirectory() as tmp:
                    out = os.path.join(tmp, "report.json")
                    result = boundary.process(case["input"], out)

                    self.assertTrue(result.ok, msg=result.error)
                    expected = legacy.compute_report(case["input"])
                    self.assertEqual(result.value, expected)
                    self.assertEqual(result.value, case["expected_report"])
                    with open(out, encoding="utf-8") as fh:
                        self.assertEqual(json.load(fh), case["expected_report"])

    def test_optional_discount_accepted(self):
        record = {
            "id": "T-2",
            "currency": "EUR",
            "discount": 0.5,
            "items": [{"name": "a", "qty": 2, "price": 5.0}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            result = boundary.process(record, os.path.join(tmp, "r.json"))
            self.assertTrue(result.ok, msg=result.error)
            self.assertEqual(result.value["discount"], 5.0)


class MissingFieldTests(unittest.TestCase):
    def _assert_rejected(self, record, needles):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "report.json")
            result = boundary.process(record, out)

            self.assertFalse(result.ok)
            self.assertEqual(result.error.code, boundary.INVALID_INPUT)
            self.assertIsNone(result.value)
            for needle in needles:
                self.assertIn(needle, result.error.message)
            self.assertFalse(os.path.exists(out), "非法输入不得写出任何文件")

    def test_record_not_object(self):
        self._assert_rejected(["not", "dict"], ["必须是 JSON 对象"])

    def test_missing_id(self):
        record = {"currency": "CNY", "items": [{"name": "a", "qty": 1, "price": 1}]}
        self._assert_rejected(record, ["id"])

    def test_missing_items(self):
        record = {"id": "x", "currency": "CNY"}
        self._assert_rejected(record, ["items"])

    def test_missing_currency(self):
        record = {"id": "x", "items": [{"name": "a", "qty": 1, "price": 1}]}
        self._assert_rejected(record, ["currency"])

    def test_missing_item_fields(self):
        record = {"id": "x", "currency": "CNY", "items": [{"name": "a"}]}
        self._assert_rejected(record, ["items[0]", "qty", "price"])


class TypeErrorTests(unittest.TestCase):
    def _assert_rejected(self, record, needles):
        with tempfile.TemporaryDirectory() as tmp:
            result = boundary.process(record, os.path.join(tmp, "r.json"))
            self.assertFalse(result.ok)
            self.assertEqual(result.error.code, boundary.INVALID_INPUT)
            for needle in needles:
                self.assertIn(needle, result.error.message)

    def test_qty_as_string(self):
        record = {
            "id": "x",
            "currency": "CNY",
            "items": [{"name": "a", "qty": "2", "price": 10}],
        }
        self._assert_rejected(record, ["qty", "数值"])

    def test_price_as_null(self):
        record = {
            "id": "x",
            "currency": "CNY",
            "items": [{"name": "a", "qty": 1, "price": None}],
        }
        self._assert_rejected(record, ["price", "数值"])

    def test_bool_is_not_number(self):
        record = {
            "id": "x",
            "currency": "CNY",
            "items": [{"name": "a", "qty": True, "price": 10}],
        }
        self._assert_rejected(record, ["qty"])

    def test_items_as_dict(self):
        record = {"id": "x", "currency": "CNY", "items": {"name": "a"}}
        self._assert_rejected(record, ["items", "数组"])

    def test_value_range_violations(self):
        record = {
            "id": "x",
            "currency": "CNY",
            "items": [{"name": "a", "qty": 0, "price": -1}],
        }
        self._assert_rejected(record, ["qty", "大于 0", "price", "大于等于 0"])

    def test_bad_currency_format(self):
        record = {
            "id": "x",
            "currency": "rmb",
            "items": [{"name": "a", "qty": 1, "price": 10}],
        }
        self._assert_rejected(record, ["currency"])

    def test_discount_out_of_range(self):
        record = {
            "id": "x",
            "currency": "CNY",
            "discount": 1.5,
            "items": [{"name": "a", "qty": 1, "price": 10}],
        }
        self._assert_rejected(record, ["discount", "[0, 1]"])


class InternalErrorIsolationTests(unittest.TestCase):
    def test_third_party_exception_becomes_result(self):
        # 格式合法（通过边界校验），但第三方模块不支持 GBP 税率表，内部抛 KeyError。
        # 异常必须被隔离，不能冒泡给调用方。
        record = {
            "id": "x",
            "currency": "GBP",
            "items": [{"name": "a", "qty": 1, "price": 10}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "report.json")
            result = boundary.process(record, out)

            self.assertFalse(result.ok)
            self.assertEqual(result.error.code, boundary.INTERNAL_ERROR)
            self.assertIn("KeyError", result.error.message)
            self.assertFalse(os.path.exists(out))

    def test_injected_arbitrary_exception_is_isolated(self):
        record = {
            "id": "x",
            "currency": "CNY",
            "items": [{"name": "a", "qty": 1, "price": 10}],
        }

        def boom(_record):
            raise RuntimeError("third party exploded")

        original = legacy.compute_report
        legacy.compute_report = boom
        try:
            with tempfile.TemporaryDirectory() as tmp:
                result = boundary.process(record, os.path.join(tmp, "r.json"))
        finally:
            legacy.compute_report = original

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, boundary.INTERNAL_ERROR)
        self.assertIn("RuntimeError", result.error.message)
        self.assertIn("exploded", result.error.message)


class AtomicWriteTests(unittest.TestCase):
    def test_existing_file_preserved_on_internal_error(self):
        record = {
            "id": "x",
            "currency": "GBP",
            "items": [{"name": "a", "qty": 1, "price": 10}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "report.json")
            with open(out, "w", encoding="utf-8") as fh:
                fh.write("PREVIOUS-GOOD-DATA")

            result = boundary.process(record, out)
            self.assertFalse(result.ok)
            with open(out, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "PREVIOUS-GOOD-DATA")

    def test_no_temp_files_left_behind_on_failure(self):
        record = {"id": "x", "currency": "GBP",
                  "items": [{"name": "a", "qty": 1, "price": 10}]}
        with tempfile.TemporaryDirectory() as tmp:
            boundary.process(record, os.path.join(tmp, "r.json"))
            leftovers = [n for n in os.listdir(tmp) if n.startswith(".tmp-")]
            self.assertEqual(leftovers, [])


class LegacyProblemDemonstrationTests(unittest.TestCase):
    """固化"重构前"问题，防止回归到旧行为。"""

    def test_legacy_leaks_exceptions(self):
        with self.assertRaises(KeyError):
            legacy.compute_report({"id": "x", "currency": "CNY"})  # 缺 items
        with self.assertRaises(TypeError):
            legacy.compute_report({
                "id": "x", "currency": "CNY",
                "items": [{"name": "a", "qty": "2", "price": 1}],
            })

    def test_legacy_writes_partial_file_then_crashes(self):
        record = {"id": "x", "items": [{"name": "a", "qty": None, "price": 1}]}
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "broken.json")
            with self.assertRaises(Exception):
                legacy.process_and_write(record, out)
            self.assertTrue(os.path.exists(out))  # 半成品文件已存在
            with open(out, encoding="utf-8") as fh:
                self.assertNotIn("]", fh.read())  # 且内容不完整


if __name__ == "__main__":
    unittest.main()
