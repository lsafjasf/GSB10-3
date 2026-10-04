"""正常加载与整体校验失败：任何一项不合法都必须保留旧配置并说明原因。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hotreload import ConfigStore, ValidationError, validate_config


def good(**overrides):
    cfg = {
        "service_name": "svc-a",
        "port": 8080,
        "timeout_ms": 500,
        "features": ["read"],
        "rate_limit": {"qps": 100, "burst": 200},
    }
    cfg.update(overrides)
    return cfg


class TestNormalLoad(unittest.TestCase):
    def test_initial_load(self):
        store = ConfigStore(good())
        cur = store.current
        self.assertEqual(cur.version, 1)
        self.assertEqual(cur.get("service_name"), "svc-a")
        self.assertEqual(cur.get("port"), 8080)

    def test_reload_bumps_version_and_swaps_whole_snapshot(self):
        store = ConfigStore(good())
        before = store.current
        new = store.load(good(port=9090, features=["read", "write"]))
        self.assertEqual(new.version, 2)
        self.assertIs(store.current, new)
        self.assertIsNot(store.current, before)
        self.assertEqual(store.get("port"), 9090)
        self.assertEqual(store.get("features"), ("read", "write"))
        # 旧快照本身不被修改
        self.assertEqual(before.get("port"), 8080)

    def test_snapshot_is_immutable(self):
        store = ConfigStore(good())
        with self.assertRaises(TypeError):
            store.current.data["port"] = 1
        with self.assertRaises(TypeError):
            store.current.data["rate_limit"]["qps"] = 1
        with self.assertRaises((TypeError, AttributeError)):
            store.current.data["features"] += ("x",)

    def test_load_json(self):
        store = ConfigStore(good())
        snap = store.load_json('{"service_name":"svc-b","port":1,"timeout_ms":1,'
                               '"features":[],"rate_limit":{"qps":1,"burst":1}}')
        self.assertEqual(snap.version, 2)
        self.assertEqual(store.get("service_name"), "svc-b")


class TestValidationFailureKeepsOldConfig(unittest.TestCase):
    def assert_rejected(self, store, bad, expect_paths):
        before = store.current
        before_version = before.version
        with self.assertRaises(ValidationError) as ctx:
            store.load(bad)
        # 旧配置原样保留（同一个对象、同一个版本号）
        self.assertIs(store.current, before)
        self.assertEqual(store.current.version, before_version)
        for path in expect_paths:
            self.assertIn(path, ctx.exception.errors,
                          f"expected error for {path!r}, got {ctx.exception.errors}")
        return ctx.exception

    def test_each_invalid_field_rejected_with_reason(self):
        cases = [
            (good(port=0), ["port"]),
            (good(port=65536), ["port"]),
            (good(port="8080"), ["port"]),
            (good(port=True), ["port"]),                      # bool 不是合法 int
            (good(timeout_ms=0), ["timeout_ms"]),
            (good(timeout_ms=60_001), ["timeout_ms"]),
            (good(timeout_ms=1.5), ["timeout_ms"]),
            (good(service_name=""), ["service_name"]),
            (good(service_name="   "), ["service_name"]),
            (good(service_name=123), ["service_name"]),
            (good(features="read"), ["features"]),
            (good(features=["read", "fly"]), ["features[1]"]),
            (good(features=["read", "read"]), ["features[1]"]),
            (good(features=[1]), ["features[0]"]),
            (good(rate_limit={"qps": 0, "burst": 1}), ["rate_limit.qps"]),
            (good(rate_limit={"qps": 1}), ["rate_limit.burst"]),
            (good(rate_limit={"qps": 1, "burst": 1, "x": 1}), ["rate_limit.x"]),
        ]
        for bad, paths in cases:
            with self.subTest(bad=bad):
                self.assert_rejected(ConfigStore(good()), bad, paths)

    def test_missing_and_unknown_keys(self):
        store = ConfigStore(good())
        bad = good()
        del bad["port"]
        del bad["rate_limit"]
        bad["debug"] = True
        err = self.assert_rejected(store, bad, ["port", "rate_limit", "debug"])
        # 所有问题一次性列出，而不是只报第一个
        self.assertEqual(len(err.errors), 3)

    def test_multiple_errors_reported_together(self):
        store = ConfigStore(good())
        err = self.assert_rejected(
            store, good(port=-1, timeout_ms="x", features=["nope"]),
            ["port", "timeout_ms", "features[0]"],
        )
        self.assertIn("port", str(err))  # 异常信息里带原因，便于运维定位

    def test_non_mapping_rejected(self):
        store = ConfigStore(good())
        self.assert_rejected(store, [1, 2, 3], ["<root>"])
        self.assert_rejected(store, "not a config", ["<root>"])
        self.assert_rejected(store, None, ["<root>"])

    def test_invalid_json_rejected(self):
        store = ConfigStore(good())
        before = store.current
        with self.assertRaises(ValidationError) as ctx:
            store.load_json("{not json")
        self.assertIs(store.current, before)
        self.assertIn("<json>", ctx.exception.errors)

    def test_initial_config_must_be_valid(self):
        with self.assertRaises(ValidationError):
            ConfigStore(good(port=0))

    def test_failed_load_does_not_poison_rollback_history(self):
        store = ConfigStore(good())
        store.load(good(port=9090))            # v2 生效
        with self.assertRaises(ValidationError):
            store.load(good(port=-1))          # 失败，不进历史
        self.assertEqual(store.history(), (1, 2))
        rolled = store.rollback()
        self.assertEqual(rolled.version, 1)    # 回滚直接到 v1，不会停在失败版本
        self.assertEqual(store.get("port"), 8080)


if __name__ == "__main__":
    unittest.main()
