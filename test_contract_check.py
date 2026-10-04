"""契约双向校验的自测（unittest，仅标准库）。

运行：python3 -m unittest test_contract_check -v
"""

import os
import unittest

from contract_check import (
    Direction,
    SchemaError,
    Severity,
    check_contract,
    check_contract_files,
    load_schema,
    load_schema_file,
)

EXAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples")


def load(name):
    return load_schema_file(os.path.join(EXAMPLES, name))


def codes(report, severity=None, direction=None):
    out = []
    for r in report.results:
        if severity and r.severity != severity:
            continue
        if direction and r.direction != direction:
            continue
        out.append(r.code)
    return out


class FullyCompatibleTest(unittest.TestCase):
    """情形一：完全兼容（含 integer 满足 number 的宽化）。"""

    def setUp(self):
        self.report = check_contract(load("consumer.json"),
                                     load("provider_compatible.json"))

    def test_compatible(self):
        self.assertTrue(self.report.compatible)

    def test_no_errors_no_warnings(self):
        self.assertEqual(codes(self.report, Severity.ERROR), [])
        self.assertEqual(codes(self.report, Severity.WARNING), [])

    def test_every_field_reported_ok(self):
        ok_paths = {r.path for r in self.report.by_severity(Severity.OK)}
        self.assertIn("id", ok_paths)
        self.assertIn("amount", ok_paths)   # integer -> number 宽化兼容
        self.assertIn("buyer.user_id", ok_paths)

    def test_incompatibility_list_empty(self):
        self.assertEqual(self.report.incompatibilities(), [])


class FieldDeletedTest(unittest.TestCase):
    """情形二：提供方删除了字段（含嵌套对象内的删除）。"""

    def setUp(self):
        self.report = check_contract(load("consumer.json"),
                                     load("provider_field_deleted.json"))

    def test_not_compatible(self):
        self.assertFalse(self.report.compatible)

    def test_missing_required_field_reported(self):
        missing = {r.path for r in self.report.results
                   if r.code == "REQUIRED_FIELD_MISSING"}
        self.assertEqual(missing, {"id", "buyer.user_id"})

    def test_missing_fields_are_errors(self):
        for r in self.report.results:
            if r.code == "REQUIRED_FIELD_MISSING":
                self.assertEqual(r.severity, Severity.ERROR)

    def test_incompatibility_list_contains_paths(self):
        paths = {r.path for r in self.report.incompatibilities()}
        self.assertIn("id", paths)
        self.assertIn("buyer.user_id", paths)


class TypeChangedTest(unittest.TestCase):
    """情形三：提供方改了字段类型（顶层、嵌套、数组元素）。"""

    def setUp(self):
        self.report = check_contract(load("consumer.json"),
                                     load("provider_type_changed.json"))

    def test_not_compatible(self):
        self.assertFalse(self.report.compatible)

    def test_type_mismatch_paths(self):
        mismatched = {r.path for r in self.report.results
                      if r.code == "TYPE_MISMATCH"
                      and r.direction == Direction.FORWARD}
        self.assertEqual(mismatched,
                         {"id", "amount", "buyer.email", "tags[]"})

    def test_mismatch_is_error_and_listed(self):
        listed = {r.path for r in self.report.incompatibilities()
                  if r.code == "TYPE_MISMATCH"}
        self.assertTrue({"id", "amount", "buyer.email", "tags[]"} <= listed)


class NewOptionalFieldTest(unittest.TestCase):
    """情形四：提供方新增可选字段（顶层 + 嵌套），消费方 tolerate。"""

    def setUp(self):
        self.report = check_contract(load("consumer.json"),
                                     load("provider_new_optional_field.json"))

    def test_still_compatible(self):
        self.assertTrue(self.report.compatible)

    def test_new_fields_reported_as_warning_not_ignored(self):
        warned = {r.path for r in self.report.results
                  if r.code == "UNKNOWN_FIELD_TOLERATED"}
        self.assertEqual(warned, {"created_at", "buyer.level"})
        for r in self.report.results:
            if r.code == "UNKNOWN_FIELD_TOLERATED":
                self.assertEqual(r.severity, Severity.WARNING)
                self.assertEqual(r.direction, Direction.REVERSE)


class StrictConsumerTest(unittest.TestCase):
    """边界：消费方策略为 strict 时，新增字段升级为 ERROR。"""

    def test_unknown_field_rejected(self):
        consumer = load_schema({
            "additional_fields": "strict",
            "fields": [{"name": "id", "type": "integer", "required": True}],
        })
        provider = load_schema({
            "fields": [
                {"name": "id", "type": "integer", "required": True},
                {"name": "extra", "type": "string"},
            ],
        })
        report = check_contract(consumer, provider)
        self.assertFalse(report.compatible)
        rejected = [r for r in report.results
                    if r.code == "UNKNOWN_FIELD_REJECTED"]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0].severity, Severity.ERROR)
        self.assertEqual(rejected[0].path, "extra")


class EdgeCaseTest(unittest.TestCase):
    """边界用例。"""

    def test_consumer_optional_field_absent_in_provider_is_info(self):
        consumer = load_schema({"fields": [
            {"name": "note", "type": "string", "required": False}]})
        provider = load_schema({"fields": []})
        report = check_contract(consumer, provider)
        self.assertTrue(report.compatible)
        infos = report.by_severity(Severity.INFO)
        self.assertEqual([r.code for r in infos], ["OPTIONAL_FIELD_ABSENT"])

    def test_integer_satisfies_number_but_not_reverse(self):
        consumer = load_schema({"fields": [
            {"name": "x", "type": "number", "required": True}]})
        provider = load_schema({"fields": [
            {"name": "x", "type": "integer", "required": True}]})
        self.assertTrue(check_contract(consumer, provider).compatible)

        consumer2 = load_schema({"fields": [
            {"name": "x", "type": "integer", "required": True}]})
        provider2 = load_schema({"fields": [
            {"name": "x", "type": "number", "required": True}]})
        self.assertFalse(check_contract(consumer2, provider2).compatible)

    def test_any_accepts_everything(self):
        consumer = load_schema({"fields": [
            {"name": "x", "type": "any", "required": True}]})
        for t in ("string", "integer", "number", "boolean", "object", "array"):
            provider = load_schema({"fields": [
                {"name": "x", "type": t, "required": True}]})
            self.assertTrue(check_contract(consumer, provider).compatible,
                            f"any 应接受 {t}")

    def test_required_became_optional_is_warning(self):
        consumer = load_schema({"fields": [
            {"name": "x", "type": "string", "required": True}]})
        provider = load_schema({"fields": [
            {"name": "x", "type": "string", "required": False}]})
        report = check_contract(consumer, provider)
        self.assertTrue(report.compatible)
        self.assertIn("REQUIRED_BECAME_OPTIONAL",
                      codes(report, Severity.WARNING))

    def test_provider_required_consumer_optional_is_warning(self):
        consumer = load_schema({"fields": [
            {"name": "x", "type": "string", "required": False}]})
        provider = load_schema({"fields": [
            {"name": "x", "type": "string", "required": True}]})
        report = check_contract(consumer, provider)
        self.assertIn("PROVIDER_REQUIRED_CONSUMER_OPTIONAL",
                      codes(report, Severity.WARNING, Direction.REVERSE))

    def test_empty_both_sides_compatible(self):
        report = check_contract(load_schema({"fields": []}),
                                load_schema({"fields": []}))
        self.assertTrue(report.compatible)
        self.assertEqual(report.results, [])

    def test_nested_missing_object_reports_whole_subtree(self):
        consumer = load_schema({"fields": [{
            "name": "a", "type": "object", "required": True,
            "fields": [{"name": "b", "type": "string", "required": True}],
        }]})
        provider = load_schema({"fields": []})
        report = check_contract(consumer, provider)
        missing = [r for r in report.results
                   if r.code == "REQUIRED_FIELD_MISSING"]
        # 整个对象 a 缺失，报 a 本身（不展开到 a.b）
        self.assertEqual([r.path for r in missing], ["a"])

    def test_deeply_nested_paths(self):
        consumer = load_schema({"fields": [{
            "name": "a", "type": "object", "required": True,
            "fields": [{
                "name": "b", "type": "object", "required": True,
                "fields": [{"name": "c", "type": "integer", "required": True}],
            }],
        }]})
        provider = load_schema({"fields": [{
            "name": "a", "type": "object", "required": True,
            "fields": [{
                "name": "b", "type": "object", "required": True,
                "fields": [{"name": "c", "type": "string", "required": True}],
            }],
        }]})
        report = check_contract(consumer, provider)
        bad = [r for r in report.results if r.code == "TYPE_MISMATCH"]
        self.assertEqual({r.path for r in bad}, {"a.b.c"})

    def test_array_item_type_mismatch(self):
        consumer = load_schema({"fields": [{
            "name": "xs", "type": "array", "required": True,
            "items": {"type": "string"}}]})
        provider = load_schema({"fields": [{
            "name": "xs", "type": "array", "required": True,
            "items": {"type": "integer"}}]})
        report = check_contract(consumer, provider)
        self.assertFalse(report.compatible)
        bad = [r for r in report.results if r.code == "TYPE_MISMATCH"]
        self.assertEqual({r.path for r in bad}, {"xs[]"})

    def test_invalid_schema_rejected(self):
        with self.assertRaises(SchemaError):
            load_schema({"fields": [{"type": "string"}]})          # 缺 name
        with self.assertRaises(SchemaError):
            load_schema({"fields": [{"name": "x", "type": "uuid"}]})  # 未知类型
        with self.assertRaises(SchemaError):
            load_schema({"additional_fields": "yolo", "fields": []})

    def test_report_summary_counts(self):
        report = check_contract(load("consumer.json"),
                                load("provider_type_changed.json"))
        summary = report.to_dict()["summary"]
        total = sum(summary.values())
        self.assertEqual(total, len(report.results))
        self.assertGreater(summary["ERROR"], 0)


class CliTest(unittest.TestCase):
    def test_cli_exit_codes(self):
        from contract_check import main
        ok = main([os.path.join(EXAMPLES, "consumer.json"),
                   os.path.join(EXAMPLES, "provider_compatible.json"),
                   "--json"])
        self.assertEqual(ok, 0)
        bad = main([os.path.join(EXAMPLES, "consumer.json"),
                    os.path.join(EXAMPLES, "provider_type_changed.json"),
                    "--json"])
        self.assertEqual(bad, 1)


if __name__ == "__main__":
    unittest.main()
