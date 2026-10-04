"""回归测试：重构前后行为必须完全一致。

golden_behavior.json 由重构前代码生成（python3 tools/dump_behavior.py legacy），
三条断言分别钉住：legacy == golden、app == golden、legacy == app。
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.dump_behavior import snapshot  # noqa: E402

GOLDEN_PATH = Path(__file__).resolve().parent / "golden_behavior.json"


class RegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        cls.before = snapshot("legacy")
        cls.after = snapshot("app")

    def test_legacy_matches_golden(self):
        self.assertEqual(self.golden, self.before)

    def test_refactored_matches_golden(self):
        self.assertEqual(self.golden, self.after)

    def test_refactored_equals_legacy(self):
        self.assertEqual(self.before, self.after)


if __name__ == "__main__":
    unittest.main()

