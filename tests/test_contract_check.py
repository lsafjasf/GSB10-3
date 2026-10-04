"""contract_check 自测：覆盖完全兼容、字段被删除、类型被改、新增可选字段及边界用例。"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contract_check import (  # noqa: E402
    Severity,
    Status,
    check_compatibility,
    load_contract,
    main,
    validate_contract,
)

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "examples")


def load(name):
    return load_contract(os.path.join(EXAMPLES, name))


class TestFullyCompatible(unittest.TestCase):
    """情形一：完全兼容（含 integer 兼容 number 的子类型规则）。"""

    def setUp(self):
        self.report = check_compatibility(load("provider_v1_compatible.json"),
                                          load("consumer_v1.json"))

    def test_compatible(self):
        self.assertTrue(self.report.compatible)
        self.assertEqual(self.report.incompatible_fields(), [])

    def test_all_forward_fields_ok(self):
        forward = [r for r in self.report.results if r.direction == "forward"]
        self.assertTrue(forward)
        self.assertTrue(all(r.status is Status.OK for r in forward))

    def test_integer_accepted_for_number(self):
        r = [r for r in self.report.results if r.path == "amount"][0]
        self.assertEqual(r.status, Status.OK)
        self.assertEqual(r.actual, "integer")
        self.assertEqual(r.expected, "number")

    def test_nested_fields_checked(self):
        paths = {r.path for r in self.report.results}
        self.assertIn("address.city", paths)
        self.assertIn("address.zip", paths)


class TestFieldDeleted(unittest.TestCase):
    """情形二：提供方删除了字段（必需 -> ERROR，可选 -> WARNING，嵌套同样生效）。"""

    def setUp(self):
        self.report = check_compatibility(load("provider_field_deleted.json"),
                                          load("consumer_v1.json"))

    def test_not_compatible(self):
        self.assertFalse(self.report.compatible)

    def test_required_missing_is_error(self):
        r = [r for r in self.report.results
             if r.path == "id" and r.direction == "forward"][0]
        self.assertEqual(r.status, Status.MISSING_FIELD)
        self.assertEqual(r.severity, Severity.ERROR)

    def test_nested_required_missing_is_error(self):
        r = [r for r in self.report.results if r.path == "address.city"][0]
        self.assertEqual(r.status, Status.MISSING_FIELD)
        self.assertEqual(r.severity, Severity.ERROR)

    def test_incompatible_list_contains_deleted_fields(self):
        paths = {r.path for r in self.report.incompatible_fields()}
        self.assertEqual(paths, {"id", "address.city"})


class TestTypeChanged(unittest.TestCase):
    """情形三：提供方修改了字段类型（含数组元素类型）。"""

    def setUp(self):
        self.report = check_compatibility(load("provider_type_changed.json"),
                                          load("consumer_v1.json"))

    def test_not_compatible(self):
        self.assertFalse(self.report.compatible)

    def test_type_mismatch_fields(self):
        mismatched = {r.path for r in self.report.results
                      if r.status is Status.TYPE_MISMATCH}
        self.assertEqual(mismatched, {"id", "status", "tags[]"})

    def test_mismatch_is_error_with_types(self):
        r = [r for r in self.report.results if r.path == "id"][0]
        self.assertEqual(r.severity, Severity.ERROR)
        self.assertEqual(r.expected, "integer")
        self.assertEqual(r.actual, "string")


class TestNewOptionalField(unittest.TestCase):
    """情形四：提供方新增可选字段（含嵌套新增），非 strict 消费方为 WARNING。"""

    def setUp(self):
        self.report = check_compatibility(load("provider_new_optional.json"),
                                          load("consumer_v1.json"))

    def test_still_compatible(self):
        self.assertTrue(self.report.compatible)

    def test_new_fields_reported_not_ignored(self):
        unexpected = {r.path for r in self.report.results
                      if r.status is Status.UNEXPECTED_FIELD}
        self.assertEqual(unexpected, {"trace_id", "address.country"})

    def test_new_fields_are_warnings(self):
        warnings = {r.path for r in self.report.by_severity(Severity.WARNING)}
        self.assertIn("trace_id", warnings)
        self.assertIn("address.country", warnings)

    def test_strict_consumer_rejects_new_fields(self):
        report = check_compatibility(load("provider_new_optional.json"),
                                     load("consumer_v1_strict.json"))
        self.assertFalse(report.compatible)
        errors = {r.path for r in report.incompatible_fields()}
        self.assertIn("trace_id", errors)
        self.assertIn("address", errors)  # strict 下整个未声明对象也是 ERROR


class TestEdgeCases(unittest.TestCase):
    """边界用例。"""

    def test_empty_contracts_are_compatible(self):
        report = check_compatibility({"fields": {}}, {"fields": {}})
        self.assertTrue(report.compatible)
        self.assertEqual(report.results, [])

    def test_empty_provider_vs_nonempty_consumer(self):
        consumer = {"fields": {"a": {"type": "string", "required": True}}}
        report = check_compatibility({"fields": {}}, consumer)
        self.assertFalse(report.compatible)
        self.assertEqual(report.results[0].status, Status.MISSING_FIELD)

    def test_any_type_accepts_everything(self):
        provider = {"fields": {"x": {"type": "object"}}}
        consumer = {"fields": {"x": {"type": "any", "required": True}}}
        report = check_compatibility(provider, consumer)
        self.assertTrue(report.compatible)

    def test_number_not_accepted_for_integer(self):
        provider = {"fields": {"x": {"type": "number"}}}
        consumer = {"fields": {"x": {"type": "integer", "required": True}}}
        report = check_compatibility(provider, consumer)
        self.assertFalse(report.compatible)
        self.assertEqual(report.results[0].status, Status.TYPE_MISMATCH)

    def test_optional_field_absent_is_warning_not_error(self):
        provider = {"fields": {}}
        consumer = {"fields": {"x": {"type": "string", "required": False}}}
        report = check_compatibility(provider, consumer)
        self.assertTrue(report.compatible)
        self.assertEqual(report.results[0].status, Status.OPTIONAL_REMOVED)
        self.assertEqual(report.results[0].severity, Severity.WARNING)

    def test_deeply_nested_object(self):
        provider = {"fields": {"a": {"type": "object", "fields": {
            "b": {"type": "object", "fields": {
                "c": {"type": "string"}}}}}}}
        consumer = {"fields": {"a": {"type": "object", "fields": {
            "b": {"type": "object", "fields": {
                "c": {"type": "integer", "required": True}}}}}}}
        report = check_compatibility(provider, consumer)
        bad = [r for r in report.results if r.path == "a.b.c"]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0].status, Status.TYPE_MISMATCH)

    def test_validate_contract_structure(self):
        self.assertEqual(validate_contract({"fields": {}}), [])
        self.assertTrue(validate_contract({}))
        self.assertTrue(validate_contract({"fields": {"x": {"type": "uuid"}}}))
        self.assertTrue(validate_contract({"fields": {"x": {}}}))
        self.assertTrue(validate_contract({"fields": {"x": "oops"}}))
        self.assertTrue(validate_contract(["not", "a", "dict"]))

    def test_report_render_contains_sections(self):
        report = check_compatibility(load("provider_type_changed.json"),
                                     load("consumer_v1.json"))
        text = report.render()
        self.assertIn("逐字段检查结果", text)
        self.assertIn("不兼容清单", text)
        self.assertIn("ERROR", text)
        self.assertIn("不兼容", text)


class TestCli(unittest.TestCase):
    def test_cli_exit_codes(self):
        ok = main([os.path.join(EXAMPLES, "provider_v1_compatible.json"),
                   os.path.join(EXAMPLES, "consumer_v1.json")])
        self.assertEqual(ok, 0)

        bad = main([os.path.join(EXAMPLES, "provider_type_changed.json"),
                    os.path.join(EXAMPLES, "consumer_v1.json")])
        self.assertEqual(bad, 1)

    def test_cli_invalid_contract_exit_2(self):
        bad_path = os.path.join(EXAMPLES, "_tmp_invalid.json")
        with open(bad_path, "w", encoding="utf-8") as fh:
            json.dump({"fields": {"x": {"type": "uuid"}}}, fh)
        try:
            rc = main([bad_path, os.path.join(EXAMPLES, "consumer_v1.json")])
            self.assertEqual(rc, 2)
        finally:
            os.remove(bad_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
