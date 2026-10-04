"""Scan packages for module-level UPPER_CASE numeric constant definitions.

A definition is classified as:
  live          referenced at least once (same-module load or cross-module import)
  dead          defined but never referenced anywhere
  test-only     defined in a test package, name absent from production sources
  test-mirror   defined in a test package, name also defined in production sources
"""

import ast
import os

TARGET_NAMES = (
    "REQUEST_TIMEOUT",
    "MAX_RETRIES",
    "CACHE_TTL",
    "BATCH_SIZE",
    "FLUSH_INTERVAL",
    "ALERT_THRESHOLD",
    "WARMUP_ROUNDS",
)

TEST_DIR_MARKERS = ("legacy_tests", "tests")


class Definition:
    def __init__(self, name, value, path, lineno):
        self.name = name
        self.value = value
        self.path = path
        self.lineno = lineno
        self.refs = 0

    @property
    def is_test_file(self):
        parts = self.path.split(os.sep)
        return any(marker in parts for marker in TEST_DIR_MARKERS)

    def classify(self, production_names):
        if self.is_test_file:
            return "test-mirror" if self.name in production_names else "test-only"
        return "live" if self.refs > 0 else "dead"

    def __repr__(self):
        return "Definition(%r, %r, %s:%d)" % (self.name, self.value, self.path, self.lineno)


def _numeric_literal(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    return None


def _module_name(root, path):
    rel = os.path.relpath(path, root)
    parts = rel[:-3].split(os.sep)  # strip .py
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_import(from_module, level, module):
    """Resolve a relative/absolute `from ... import` to a dotted module name."""
    if level == 0:
        return module or ""
    parts = from_module.split(".")[:-1]  # package of the importing module
    if level > 1:
        parts = parts[: -(level - 1)]
    if module:
        parts = parts + module.split(".")
    return ".".join(parts)


def scan_package(root, package_dirs):
    """Return list of Definition for every target constant in package_dirs."""
    definitions = []
    trees = {}
    for package in package_dirs:
        package_root = os.path.join(root, package)
        for dirpath, _dirnames, filenames in os.walk(package_root):
            for filename in sorted(filenames):
                if not filename.endswith(".py"):
                    continue
                path = os.path.join(dirpath, filename)
                with open(path, "r", encoding="utf-8") as handle:
                    tree = ast.parse(handle.read(), filename=path)
                trees[path] = tree
                for node in tree.body:
                    if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                        continue
                    target = node.targets[0]
                    if not (isinstance(target, ast.Name) and target.id.isupper()):
                        continue
                    if target.id not in TARGET_NAMES:
                        continue
                    value = _numeric_literal(node.value)
                    if value is None:
                        continue
                    definitions.append(Definition(target.id, value, path, node.lineno))

    by_file = {}
    for definition in definitions:
        by_file.setdefault(definition.path, {})[definition.name] = definition
    module_to_path = {_module_name(root, path): path for path in trees}

    for path, tree in trees.items():
        local = by_file.get(path, {})
        from_module = _module_name(root, path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                definition = local.get(node.id)
                if definition is not None:
                    definition.refs += 1
            elif isinstance(node, ast.ImportFrom):
                imported_module = _resolve_import(from_module, node.level, node.module)
                imported_path = module_to_path.get(imported_module)
                if imported_path is None:
                    continue
                for alias in node.names:
                    definition = by_file.get(imported_path, {}).get(alias.name)
                    if definition is not None:
                        definition.refs += 1
    return definitions


def production_names(definitions):
    return {d.name for d in definitions if not d.is_test_file}
