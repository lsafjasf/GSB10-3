"""注册表守卫：取值与对照表一致、元数据自洽、阈值不再散落。"""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import thresholds  # noqa: E402

# 与 docs/THRESHOLDS.md 对照表一致的最终取值。
EXPECTED_VALUES = {
    "HTTP_REQUEST_TIMEOUT_SECONDS": 30,
    "EXPORT_REQUEST_TIMEOUT_SECONDS": 45,
    "MAX_RETRIES": 3,
    "RATE_LIMIT_PER_MINUTE": 100,
    "JOB_TIMEOUT_SECONDS": 300,
    "INGEST_BATCH_SIZE": 500,
    "EXPORT_BATCH_SIZE": 1000,
    "SESSION_TIMEOUT_MINUTES": 30,
    "MAX_LOGIN_ATTEMPTS": 5,
    "MAX_DISCOUNT": 0.30,
    "BULK_THRESHOLD": 20,
    "SMS_LENGTH_LIMIT": 160,
}

# 允许保留的模块级常量：仅测试使用的夹具，不属于业务阈值。
ALLOWED_MODULE_CONSTANTS = {("test_helpers.py", "FIXTURE_RATE_LIMIT")}


def is_constant_name(name):
    return name.isupper() and not name.startswith("_")


class RegistryValueTest(unittest.TestCase):
    def test_values_match_agreed_table(self):
        for name, expected in EXPECTED_VALUES.items():
            self.assertEqual(getattr(thresholds, name), expected, name)

    def test_registry_covers_all_entries(self):
        self.assertEqual(set(thresholds.REGISTRY), set(EXPECTED_VALUES))

    def test_registry_metadata_consistent(self):
        for name, meta in thresholds.REGISTRY.items():
            self.assertEqual(meta["value"], getattr(thresholds, name), name)
            self.assertTrue(meta["former_locations"], name)


class NoScatteredConstantsTest(unittest.TestCase):
    def test_no_module_level_constants_outside_registry(self):
        app_dir = ROOT / "app"
        offenders = []
        for path in sorted(app_dir.glob("*.py")):
            if path.name in ("__init__.py", "thresholds.py"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (isinstance(target, ast.Name)
                                and is_constant_name(target.id)
                                and (path.name, target.id) not in ALLOWED_MODULE_CONSTANTS):
                            offenders.append("{}:{} {}".format(
                                path.name, node.lineno, target.id))
        self.assertEqual(offenders, [])


class ValidateTest(unittest.TestCase):
    def test_validate_passes(self):
        self.assertTrue(thresholds.validate())

    def test_validate_rejects_tampered_value(self):
        original = thresholds.MAX_DISCOUNT
        thresholds.MAX_DISCOUNT = 1.5
        try:
            with self.assertRaises(ValueError):
                thresholds.validate()
        finally:
            thresholds.MAX_DISCOUNT = original


if __name__ == "__main__":
    unittest.main()

