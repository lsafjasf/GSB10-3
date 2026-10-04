"""Source-shape checks on the refactored app package."""

import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(ROOT, "app")
THRESHOLD_NAMES = {
    "REQUEST_TIMEOUT",
    "MAX_RETRIES",
    "CACHE_TTL",
    "BATCH_SIZE",
    "FLUSH_INTERVAL",
    "ALERT_THRESHOLD",
}


def app_modules():
    for filename in sorted(os.listdir(APP_DIR)):
        if filename.endswith(".py") and filename != "__init__.py":
            yield os.path.join(APP_DIR, filename)


class NoStrayDefinitionsTest(unittest.TestCase):
    def test_thresholds_defined_only_in_registry_module(self):
        for path in app_modules():
            if path.endswith("thresholds.py"):
                continue
            with self.subTest(module=os.path.basename(path)):
                with open(path, encoding="utf-8") as handle:
                    tree = ast.parse(handle.read(), filename=path)
                names = [
                    target.id
                    for node in tree.body
                    if isinstance(node, ast.Assign)
                    for target in node.targets
                    if isinstance(target, ast.Name)
                ]
                self.assertEqual(set(names) & THRESHOLD_NAMES, set())

    def test_used_thresholds_are_imported_from_registry(self):
        for path in app_modules():
            if path.endswith("thresholds.py"):
                continue
            with self.subTest(module=os.path.basename(path)):
                with open(path, encoding="utf-8") as handle:
                    tree = ast.parse(handle.read(), filename=path)
                loaded = {
                    node.id
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Name)
                    and isinstance(node.ctx, ast.Load)
                    and node.id in THRESHOLD_NAMES
                }
                imported = set()
                for node in tree.body:
                    if (
                        isinstance(node, ast.ImportFrom)
                        and node.module == "thresholds"
                    ):
                        imported.update(alias.name for alias in node.names)
                self.assertEqual(loaded, loaded & imported)

    def test_registry_exports_every_threshold(self):
        namespace = {}
        with open(os.path.join(APP_DIR, "thresholds.py"), encoding="utf-8") as handle:
            exec(compile(handle.read(), "thresholds.py", "exec"), namespace)
        for name in THRESHOLD_NAMES:
            self.assertIn(name, namespace["__all__"])
            self.assertIn(name, namespace["REGISTRY"])


if __name__ == "__main__":
    unittest.main()
