"""Guard the consolidation itself.

These tests turn the inventory/adoption analysis into executable checks:
legacy baseline is frozen, every live legacy value must equal the adopted
registry value, and the refactored package must contain no stray numeric
threshold redefinitions.
"""

import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import thresholds
from tools import scanner

# Frozen pre-refactor inventory: (name, path, value, classification).
EXPECTED_INVENTORY = {
    ("REQUEST_TIMEOUT", "legacy/client.py", 30, "live"),
    ("REQUEST_TIMEOUT", "legacy/api.py", 30, "live"),
    ("REQUEST_TIMEOUT", "legacy/report.py", 30, "live"),
    ("REQUEST_TIMEOUT", "legacy/settings.py", 30, "dead"),
    ("MAX_RETRIES", "legacy/client.py", 3, "live"),
    ("MAX_RETRIES", "legacy/worker.py", 5, "dead"),
    ("MAX_RETRIES", "legacy/settings.py", 3, "dead"),
    ("CACHE_TTL", "legacy/cache.py", 300, "live"),
    ("CACHE_TTL", "legacy/api.py", 360, "dead"),
    ("CACHE_TTL", "legacy/settings.py", 300, "dead"),
    ("BATCH_SIZE", "legacy/aggregator.py", 100, "live"),
    ("BATCH_SIZE", "legacy/worker.py", 100, "live"),
    ("BATCH_SIZE", "legacy/settings.py", 100, "dead"),
    ("FLUSH_INTERVAL", "legacy/aggregator.py", 60, "live"),
    ("FLUSH_INTERVAL", "legacy/alerting.py", 60, "live"),
    ("FLUSH_INTERVAL", "legacy/settings.py", 60, "dead"),
    ("ALERT_THRESHOLD", "legacy/alerting.py", 0.95, "live"),
    ("ALERT_THRESHOLD", "legacy/report.py", 0.95, "live"),
    ("ALERT_THRESHOLD", "legacy/settings.py", 0.9, "dead"),
    ("ALERT_THRESHOLD", "legacy_tests/test_alert.py", 0.95, "test-mirror"),
    ("WARMUP_ROUNDS", "legacy_tests/test_pipeline.py", 2, "test-only"),
}

ADOPTED = {
    "REQUEST_TIMEOUT": 30,
    "MAX_RETRIES": 3,
    "CACHE_TTL": 300,
    "BATCH_SIZE": 100,
    "FLUSH_INTERVAL": 60,
    "ALERT_THRESHOLD": 0.95,
}

# Values found on live paths must be unique per constant, i.e. there is no
# live-vs-live conflict the adoption rule cannot resolve silently.
EXPECTED_LIVE_VALUES = {
    "REQUEST_TIMEOUT": {30},
    "MAX_RETRIES": {3},
    "CACHE_TTL": {300},
    "BATCH_SIZE": {100},
    "FLUSH_INTERVAL": {60},
    "ALERT_THRESHOLD": {0.95},
}

EXPECTED_CONFLICTS = {
    # constant: {(dropped path, dropped value)}
    "MAX_RETRIES": {("legacy/worker.py", 5)},
    "CACHE_TTL": {("legacy/api.py", 360)},
    "ALERT_THRESHOLD": {("legacy/settings.py", 0.9)},
}


def baseline_definitions():
    definitions = scanner.scan_package(ROOT, ("legacy", "legacy_tests"))
    prod = scanner.production_names(definitions)
    return definitions, prod


class BaselineInventoryTest(unittest.TestCase):
    def test_inventory_matches_frozen_snapshot(self):
        definitions, prod = baseline_definitions()
        actual = {
            (d.name, os.path.relpath(d.path, ROOT), d.value, d.classify(prod))
            for d in definitions
        }
        self.assertEqual(actual, EXPECTED_INVENTORY)

    def test_definition_count_and_file_count(self):
        definitions, _ = baseline_definitions()
        self.assertEqual(len(definitions), 21)
        self.assertEqual(
            len({os.path.relpath(d.path, ROOT) for d in definitions}), 10
        )


class AdoptionTest(unittest.TestCase):
    def test_adopted_values_match_registry(self):
        for name, value in ADOPTED.items():
            self.assertEqual(getattr(thresholds, name), value)

    def test_registry_metadata_values_agree_with_constants(self):
        for name, entry in thresholds.REGISTRY.items():
            self.assertEqual(entry["value"], getattr(thresholds, name), name)

    def test_live_legacy_values_are_unique_and_equal_adopted_value(self):
        definitions, prod = baseline_definitions()
        for name, expected in EXPECTED_LIVE_VALUES.items():
            live_values = {
                d.value
                for d in definitions
                if d.name == name and d.classify(prod) == "live"
            }
            self.assertEqual(live_values, expected, name)
            self.assertEqual(live_values, {ADOPTED[name]}, name)

    def test_conflicts_are_recorded_in_registry_metadata(self):
        for name, expected_dropped in EXPECTED_CONFLICTS.items():
            dropped = {
                (path, value)
                for path, value in thresholds.REGISTRY[name]["dropped"]
                if path.startswith("legacy/") and path != "legacy/settings.py"
                or name == "ALERT_THRESHOLD"
                and path == "legacy/settings.py"
            }
            self.assertEqual(dropped, expected_dropped, name)

    def test_dead_settings_drift_is_dropped(self):
        # settings.py drifted ALERT_THRESHOLD to 0.9; it must not be adopted.
        self.assertEqual(thresholds.ALERT_THRESHOLD, 0.95)

    def test_test_only_constant_is_not_in_registry(self):
        self.assertFalse(hasattr(thresholds, "WARMUP_ROUNDS"))
        self.assertNotIn("WARMUP_ROUNDS", thresholds.REGISTRY)

    def test_registry_is_immutable(self):
        with self.assertRaises(TypeError):
            thresholds.REGISTRY["BATCH_SIZE"] = {"value": 999}
