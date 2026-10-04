"""边界用例：取值边界、类型陷阱、不可变性、文件监听热加载。"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hotreload import ConfigStore, JsonFileWatcher, ValidationError


def good(**overrides):
    cfg = {
        "service_name": "svc",
        "port": 8080,
        "timeout_ms": 500,
        "features": ["read"],
        "rate_limit": {"qps": 100, "burst": 200},
    }
    cfg.update(overrides)
    return cfg


class TestBoundaryValues(unittest.TestCase):
    def test_min_and_max_accepted(self):
        store = ConfigStore(good(port=1, timeout_ms=1))
        snap = store.load(good(port=65535, timeout_ms=60_000))
        self.assertEqual(snap.get("port"), 65535)
        self.assertEqual(snap.get("timeout_ms"), 60_000)

    def test_just_outside_range_rejected(self):
        for bad in (good(port=0), good(port=65536),
                    good(timeout_ms=0), good(timeout_ms=60_001)):
            with self.subTest(bad=bad):
                store = ConfigStore(good())
                with self.assertRaises(ValidationError):
                    store.load(bad)
                self.assertEqual(store.current.version, 1)

    def test_bool_is_not_int(self):
        for key in ("port", "timeout_ms"):
            store = ConfigStore(good())
            with self.assertRaises(ValidationError):
                store.load(good(**{key: True}))
        store = ConfigStore(good())
        with self.assertRaises(ValidationError):
            store.load(good(rate_limit={"qps": False, "burst": 1}))

    def test_float_and_string_numbers_rejected(self):
        store = ConfigStore(good())
        for key in ("port", "timeout_ms"):
            for v in (1.0, "8080", None):
                with self.assertRaises(ValidationError):
                    store.load(good(**{key: v}))

    def test_empty_features_allowed(self):
        store = ConfigStore(good())
        snap = store.load(good(features=[]))
        self.assertEqual(snap.get("features"), ())

    def test_unicode_service_name(self):
        store = ConfigStore(good())
        snap = store.load(good(service_name="服务-甲"))
        self.assertEqual(snap.get("service_name"), "服务-甲")

    def test_extra_nested_keys_rejected(self):
        store = ConfigStore(good())
        with self.assertRaises(ValidationError) as ctx:
            store.load(good(rate_limit={"qps": 1, "burst": 1, "window": 10}))
        self.assertIn("rate_limit.window", ctx.exception.errors)


class TestImmutability(unittest.TestCase):
    def test_caller_mutation_after_load_does_not_leak(self):
        raw = good()
        store = ConfigStore(raw)
        raw["port"] = 1                       # 调用方之后改自己的 dict
        raw["features"].append("admin")
        raw["rate_limit"]["qps"] = 1
        self.assertEqual(store.get("port"), 8080)
        self.assertEqual(store.get("features"), ("read",))
        self.assertEqual(store.get("rate_limit")["qps"], 100)

    def test_snapshot_cannot_be_modified(self):
        store = ConfigStore(good())
        snap = store.current
        for mutate in (
            lambda: snap.data.__setitem__("port", 1),
            lambda: snap.data["rate_limit"].__setitem__("qps", 1),
        ):
            with self.assertRaises((TypeError, AttributeError)):
                mutate()
        self.assertIsInstance(snap.get("features"), tuple)


class TestJsonFileWatcher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"
        self._write(good())
        self.store = ConfigStore(good())
        self.watcher = JsonFileWatcher(self.store, str(self.path), interval=0.05)

    def tearDown(self):
        self.watcher.stop()
        self.tmp.cleanup()

    def _write(self, obj):
        # 先写临时文件再 rename，避免监听线程读到写了一半的文件
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj), encoding="utf-8")
        tmp.replace(self.path)

    def _wait_version(self, version, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.store.current.version >= version:
                return True
            time.sleep(0.02)
        return False

    def test_valid_change_is_hot_loaded(self):
        self.watcher.start()
        self._write(good(port=9090))
        self.assertTrue(self._wait_version(2), "config was not hot loaded")
        self.assertEqual(self.store.get("port"), 9090)
        self.assertIsNone(self.watcher.last_error)

    def test_invalid_change_keeps_old_config_and_recovers(self):
        self.watcher.start()
        self._write(good(port=9090))
        self.assertTrue(self._wait_version(2))

        self._write(good(port=-1))             # 非法：必须保留 v2
        time.sleep(0.3)
        self.assertEqual(self.store.current.version, 2)
        self.assertEqual(self.store.get("port"), 9090)
        self.assertIsInstance(self.watcher.last_error, ValidationError)

        self._write(good(port=7070))           # 修复后继续生效
        self.assertTrue(self._wait_version(3), "watcher did not recover")
        self.assertEqual(self.store.get("port"), 7070)

    def test_broken_json_keeps_old_config(self):
        self.watcher.start()
        self.path.write_text("{broken", encoding="utf-8")
        time.sleep(0.3)
        self.assertEqual(self.store.current.version, 1)
        self.assertIsNotNone(self.watcher.last_error)

    def test_missing_file_keeps_old_config(self):
        self.watcher.start()
        self.path.unlink()
        time.sleep(0.3)
        self.assertEqual(self.store.current.version, 1)
        self.assertIsNotNone(self.watcher.last_error)
        self._write(good(port=6060))           # 文件恢复后继续热加载
        self.assertTrue(self._wait_version(2))

    def test_unchanged_file_is_not_reloaded(self):
        self.watcher.start()
        time.sleep(0.3)
        self.assertEqual(self.store.current.version, 1)
        self.assertEqual(self.watcher.reload_count, 0)

    def test_reload_now_is_synchronous(self):
        self._write(good(port=5050))
        self.assertTrue(self.watcher.reload_now())
        self.assertEqual(self.store.get("port"), 5050)
        self.assertFalse(self.watcher.reload_now())  # 内容未变，不重复加载


if __name__ == "__main__":
    unittest.main()
