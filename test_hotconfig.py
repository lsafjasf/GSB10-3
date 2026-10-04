"""hotconfig 自测：正常加载 / 校验失败 / 并发读取原子性 / 回滚 / 边界用例。

运行：python3 -m unittest -v test_hotconfig
"""

import json
import os
import tempfile
import threading
import time
import unittest

from hotconfig import ConfigError, Field, HotConfig, Schema


def make_schema():
    return Schema([
        Field("host", str),
        Field("port", int, min_value=1, max_value=65535),
        Field("debug", bool, required=False, default=False),
        Field("mode", str, choices=("dev", "staging", "prod")),
        Field("tags", list, required=False, default=[]),
    ], allow_unknown=False)


def valid_config(port=8080, mode="prod"):
    return {"host": "127.0.0.1", "port": port, "mode": mode}


class TestNormalLoad(unittest.TestCase):
    """情形一：正常加载。"""

    def test_load_from_dict(self):
        hot = HotConfig(schema=make_schema())
        snap = hot.reload_from_dict(valid_config())
        self.assertEqual(snap.version, 1)
        self.assertEqual(hot.get("host"), "127.0.0.1")
        self.assertEqual(hot.get("port"), 8080)
        self.assertEqual(hot.get("debug"), False)   # 默认值被填充
        self.assertEqual(hot.get("tags"), ())

    def test_load_from_file_and_reload(self):
        hot = HotConfig(schema=make_schema(), initial=valid_config(port=1))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "app.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(valid_config(port=9090, mode="dev"), fh)
            snap = hot.reload_from_file(path)
        self.assertEqual(snap.version, 2)
        self.assertEqual(hot.get("port"), 9090)
        self.assertEqual(hot.get("mode"), "dev")
        self.assertEqual(hot.versions, [1, 2])


class TestValidationFailure(unittest.TestCase):
    """情形二：校验失败——任何一项不合法都保留旧配置，并说明全部原因。"""

    def setUp(self):
        self.hot = HotConfig(schema=make_schema(), initial=valid_config(port=8080))

    def test_invalid_config_keeps_old_and_reports_all_reasons(self):
        bad = {
            "host": 123,                 # 类型错误
            "port": 70000,               # 超上限
            "mode": "production",        # 不在 choices
            "unknown_key": True,         # 未知项
        }
        with self.assertRaises(ConfigError) as ctx:
            self.hot.reload_from_dict(bad)
        reasons = ctx.exception.reasons
        # 所有问题一次性报出，而不是只报第一个
        self.assertTrue(any("host" in r and "类型" in r for r in reasons), reasons)
        self.assertTrue(any("port" in r and "过大" in r for r in reasons), reasons)
        self.assertTrue(any("mode" in r and "取值非法" in r for r in reasons), reasons)
        self.assertTrue(any("unknown_key" in r for r in reasons), reasons)
        # 旧配置原样保留
        self.assertEqual(self.hot.version, 1)
        self.assertEqual(self.hot.get("port"), 8080)
        self.assertEqual(self.hot.get("host"), "127.0.0.1")

    def test_missing_required_field_keeps_old(self):
        with self.assertRaises(ConfigError) as ctx:
            self.hot.reload_from_dict({"port": 1, "mode": "dev"})
        self.assertTrue(any("host" in r for r in ctx.exception.reasons))
        self.assertEqual(self.hot.get("host"), "127.0.0.1")

    def test_custom_validator_failure_keeps_old(self):
        def no_localhost_in_prod(data):
            if data.get("mode") == "prod" and data.get("host") == "127.0.0.1":
                return ["prod 模式不允许绑定 127.0.0.1"]
            return []

        hot = HotConfig(schema=make_schema(), validator=no_localhost_in_prod,
                        initial=valid_config(mode="dev"))
        with self.assertRaises(ConfigError) as ctx:
            hot.reload_from_dict(valid_config(mode="prod"))
        self.assertIn("prod 模式不允许", str(ctx.exception))
        self.assertEqual(hot.get("mode"), "dev")

    def test_failed_reload_does_not_consume_history(self):
        with self.assertRaises(ConfigError):
            self.hot.reload_from_dict({"host": 1})
        self.hot.reload_from_dict(valid_config(port=2))
        self.assertEqual(self.hot.versions, [1, 2])  # 失败没有留下垃圾版本


class TestAtomicity(unittest.TestCase):
    """情形三：加载过程中反复读取——读取方永远看到完整的一个版本。

    断言构造：每个版本的 host 与 port 是绑定的一对（v1: A/1, v2: B/2, ...）。
    如果出现半新半旧，就会读到 host 属于版本 i 而 port 属于版本 j（i != j）。
    此外校验快照对象身份：一次 current 读取拿到的必是某一个完整快照。
    """

    def test_concurrent_reads_during_reload_never_see_mixed_state(self):
        pairs = [(f"host-{i}", i) for i in range(200)]
        schema = Schema([Field("host", str), Field("port", int)])
        hot = HotConfig(schema=schema, initial={"host": pairs[0][0], "port": pairs[0][1]})

        stop = threading.Event()
        violations = []
        read_count = [0]

        def reader():
            expected = dict(pairs)
            while not stop.is_set():
                snap = hot.current
                # 原子性断言：同一快照内的 host/port 必须属于同一版本
                if expected.get(snap["host"]) != snap["port"]:
                    violations.append((snap["host"], snap["port"], snap.version))
                read_count[0] += 1

        readers = [threading.Thread(target=reader) for _ in range(8)]
        for t in readers:
            t.start()
        for host, port in pairs[1:]:
            hot.reload_from_dict({"host": host, "port": port})
        stop.set()
        for t in readers:
            t.join()

        self.assertGreater(read_count[0], 0)
        self.assertEqual(violations, [])
        self.assertEqual(hot.version, len(pairs))

    def test_reader_holds_old_snapshot_after_swap(self):
        hot = HotConfig(schema=make_schema(), initial=valid_config(port=1))
        before = hot.current
        hot.reload_from_dict(valid_config(port=2))
        # 切换前拿到的引用不受切换影响，仍是完整的旧版本
        self.assertEqual(before.version, 1)
        self.assertEqual(before["port"], 1)
        self.assertEqual(hot.current["port"], 2)
        self.assertIsNot(before, hot.current)

    def test_concurrent_reload_and_rollback_stay_consistent(self):
        """多个写线程并发 reload/rollback，读取方仍只见完整版本。"""
        schema = Schema([Field("host", str), Field("port", int)])
        hot = HotConfig(schema=schema, initial={"host": "h0", "port": 0})
        errors = []

        def writer(wid):
            try:
                for i in range(50):
                    hot.reload_from_dict({"host": f"w{wid}-{i}", "port": i})
                    if i % 3 == 0:
                        try:
                            hot.rollback()
                        except ConfigError:
                            pass
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        def reader():
            while not stop.is_set():
                snap = hot.current
                host, port = snap["host"], snap["port"]
                # host 形如 wX-N，port 必须等于 N
                if host != "h0" and int(host.rsplit("-", 1)[1]) != port:
                    errors.append(AssertionError(f"混合状态: {host} vs {port}"))

        stop = threading.Event()
        writers = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        readers = [threading.Thread(target=reader) for _ in range(4)]
        for t in readers + writers:
            t.start()
        for t in writers:
            t.join()
        stop.set()
        for t in readers:
            t.join()
        self.assertEqual(errors, [])


class TestRollback(unittest.TestCase):
    """情形四：回滚——记录回滚前后的版本与读取结果。"""

    def test_rollback_restores_previous_version(self):
        hot = HotConfig(schema=make_schema(), initial=valid_config(port=1))
        hot.reload_from_dict(valid_config(port=2))
        hot.reload_from_dict(valid_config(port=3))
        self.assertEqual((hot.version, hot.get("port")), (3, 3))  # 回滚前

        snap = hot.rollback()
        self.assertEqual((snap.version, hot.get("port")), (2, 2))  # 第一次回滚

        snap = hot.rollback()
        self.assertEqual((snap.version, hot.get("port")), (1, 1))  # 第二次回滚

        with self.assertRaises(ConfigError):  # 没有更早的历史了
            hot.rollback()
        self.assertEqual((hot.version, hot.get("port")), (1, 1))  # 状态未被破坏

    def test_rollback_to_specific_version(self):
        hot = HotConfig(schema=make_schema(), initial=valid_config(port=1))
        hot.reload_from_dict(valid_config(port=2))
        hot.reload_from_dict(valid_config(port=3))
        snap = hot.rollback_to(1)
        self.assertEqual((snap.version, hot.get("port")), (1, 1))
        self.assertEqual(hot.versions, [1])

    def test_rollback_after_failed_reload(self):
        """新配置校验失败 -> 直接回滚仍回到上一个「生效」版本。"""
        hot = HotConfig(schema=make_schema(), initial=valid_config(port=1))
        hot.reload_from_dict(valid_config(port=2))
        with self.assertRaises(ConfigError):
            hot.reload_from_dict({"host": 1})  # 失败，未生效
        snap = hot.rollback()
        self.assertEqual((snap.version, hot.get("port")), (1, 1))

    def test_reload_after_rollback_gets_fresh_version(self):
        hot = HotConfig(schema=make_schema(), initial=valid_config(port=1))
        hot.reload_from_dict(valid_config(port=2))
        hot.rollback()
        snap = hot.reload_from_dict(valid_config(port=99))
        self.assertEqual(snap.version, 3)  # 版本号单调递增，不复用
        self.assertEqual(hot.versions, [1, 3])


class TestEdgeCases(unittest.TestCase):
    """边界用例。"""

    def test_malformed_json_file_keeps_old(self):
        hot = HotConfig(schema=make_schema(), initial=valid_config())
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"host": "x",')  # 截断的 JSON
            with self.assertRaises(ConfigError) as ctx:
                hot.reload_from_file(path)
        self.assertIn("JSON", str(ctx.exception))
        self.assertEqual(hot.get("port"), 8080)

    def test_missing_file_keeps_old(self):
        hot = HotConfig(schema=make_schema(), initial=valid_config())
        with self.assertRaises(ConfigError) as ctx:
            hot.reload_from_file("/nonexistent/dir/config.json")
        self.assertIn("读取配置文件失败", str(ctx.exception))
        self.assertEqual(hot.version, 1)

    def test_top_level_not_dict(self):
        hot = HotConfig(schema=make_schema(), initial=valid_config())
        for bad in ([1, 2], "str", 42, None):
            with self.assertRaises(ConfigError):
                hot.reload_from_dict(bad)
        self.assertEqual(hot.version, 1)

    def test_empty_config_rejected_when_required_fields(self):
        hot = HotConfig(schema=make_schema(), initial=valid_config())
        with self.assertRaises(ConfigError) as ctx:
            hot.reload_from_dict({})
        self.assertTrue(any("host" in r for r in ctx.exception.reasons))
        self.assertTrue(any("port" in r for r in ctx.exception.reasons))
        self.assertTrue(any("mode" in r for r in ctx.exception.reasons))

    def test_no_schema_accepts_any_dict(self):
        hot = HotConfig(initial={"a": 1})
        hot.reload_from_dict({"anything": [1, {"x": 2}]})
        self.assertEqual(hot.get("anything"), (1, {"x": 2}))

    def test_current_before_any_load_raises(self):
        hot = HotConfig(schema=make_schema())
        with self.assertRaises(ConfigError):
            _ = hot.current

    def test_invalid_initial_config_raises_at_construction(self):
        with self.assertRaises(ConfigError):
            HotConfig(schema=make_schema(), initial={"host": 1})

    def test_rollback_to_unknown_version(self):
        hot = HotConfig(schema=make_schema(), initial=valid_config())
        with self.assertRaises(ConfigError) as ctx:
            hot.rollback_to(99)
        self.assertIn("99", str(ctx.exception))

    def test_snapshot_is_immutable(self):
        hot = HotConfig(schema=make_schema(), initial=valid_config())
        snap = hot.current
        with self.assertRaises(AttributeError):
            snap.version = 99
        with self.assertRaises(TypeError):
            snap.data["port"] = 1  # 只读映射

    def test_caller_mutation_after_reload_does_not_leak(self):
        """reload 后调用方再改自己的 dict，不影响已生效快照。"""
        hot = HotConfig(schema=make_schema())
        data = valid_config()
        data["tags"] = ["a"]
        hot.reload_from_dict(data)
        data["port"] = 1
        data["tags"].append("b")
        self.assertEqual(hot.get("port"), 8080)
        self.assertEqual(hot.get("tags"), ("a",))

    def test_unicode_and_nested_config(self):
        hot = HotConfig()
        hot.reload_from_dict({"名称": "服务", "nested": {"列表": [1, {"深": True}]}})
        self.assertEqual(hot.get("名称"), "服务")
        self.assertEqual(hot.get("nested")["列表"][1]["深"], True)

    def test_type_error_does_not_cascade(self):
        """类型错误时不应再报 min/max 等次级错误，避免误导。"""
        hot = HotConfig(schema=make_schema(), initial=valid_config())
        with self.assertRaises(ConfigError) as ctx:
            hot.reload_from_dict({"host": "h", "port": "not-a-number", "mode": "dev"})
        port_errors = [r for r in ctx.exception.reasons if "port" in r]
        self.assertEqual(len(port_errors), 1)
        self.assertIn("类型", port_errors[0])

    def test_custom_validator_exception_counts_as_failure(self):
        def broken(data):
            if data.get("port") == 2:
                raise RuntimeError("boom")
            return []

        hot = HotConfig(schema=make_schema(), validator=broken, initial=valid_config())
        with self.assertRaises(ConfigError) as ctx:
            hot.reload_from_dict(valid_config(port=2))
        self.assertIn("boom", str(ctx.exception))
        self.assertEqual(hot.get("port"), 8080)


if __name__ == "__main__":
    unittest.main()
