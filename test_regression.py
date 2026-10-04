"""回归 + 边界用例（仅用标准库 unittest）。

覆盖：
  A. 逐个调用点：重构前行为 == 重构后行为（plan 快照逐字段相等）
  B. 旧调用形式：新 ExportJob 走位置参数兼容层，行为不变
  C. 只传必填项：默认值与旧构造函数完全一致
  D. 传全部项：25 个参数全部显式指定
  E. 非法组合：构造之前（创建配置时）即抛 ConfigError，Job 不被构造
  F. 边界值：1/9、0、1024 等临界点
"""
import unittest

from callsites import CALLSITES
from config import ExportConfig, ConfigError
from job import ExportJob
from legacy_job import ExportJob as LegacyExportJob

REQUIRED = dict(name="t", source="/s", dest="/d")


class CallSiteRegression(unittest.TestCase):
    """A. 每个调用点重构前后行为一致。"""

    def test_callsite_plans_identical(self):
        for label, before, after in CALLSITES:
            with self.subTest(callsite=label):
                self.assertEqual(before().plan(), after().plan())

    def test_legacy_positional_form_on_new_class(self):
        """B. 新类仍接受旧的位置参数形式，结果与旧类一致。"""
        for label, before, after in CALLSITES:
            with self.subTest(callsite=label):
                legacy_plan = before().plan()
                # before() 用的是旧类；这里把同样的位置参数交给新类兼容层
                shim = ExportJob(*_positional_args_for(label))
                self.assertEqual(shim.plan(), legacy_plan)

    def test_callsite_attrs_match(self):
        """配置字段本身也与旧类逐属性一致（不只是 plan 派生结果）。"""
        for label, before, after in CALLSITES:
            with self.subTest(callsite=label):
                old = before()
                cfg = after().config
                for field in _FIELD_NAMES:
                    self.assertEqual(
                        getattr(cfg, field), getattr(old, field),
                        msg=f"{label}: 字段 {field} 不一致")


class RequiredOnlyAndAll(unittest.TestCase):
    def test_required_only_matches_legacy_defaults(self):
        """C. 只传必填项：配置对象默认值 = 旧构造函数默认位置默认值。"""
        new_plan = ExportJob(ExportConfig(**REQUIRED)).plan()
        old_plan = LegacyExportJob("t", "/s", "/d").plan()
        self.assertEqual(new_plan, old_plan)

    def test_all_params_roundtrip(self):
        """D. 25 项全传：新形式与旧形式逐项相等。"""
        cfg = ExportConfig(
            name="full", source="/s", dest="/d", fmt="tsv", delimiter="\t",
            encoding="utf-16", compress=True, compression_level=1,
            retry_count=1, retry_interval=0.5, timeout=0.5, batch_size=1,
            max_file_size_mb=1, include_header=False, skip_empty=False,
            log_level="ERROR", notify_email="a@b.c", notify_on_failure=True,
            schedule="*/5 * * * *", overwrite=False, dry_run=False,
            checksum=False, temp_dir="/x", max_workers=1, buffer_size=1024)
        self.assertEqual(
            ExportJob(cfg).plan(),
            LegacyExportJob(
                "full", "/s", "/d", "tsv", "\t", "utf-16", True, 1,
                1, 0.5, 0.5, 1, 1, False, False, "ERROR", "a@b.c",
                True, "*/5 * * * *", False, False, False, "/x", 1, 1024
            ).plan())

    def test_missing_required_raises(self):
        with self.assertRaises(TypeError):
            ExportConfig(name="t", source="/s")  # 缺 dest


class InvalidCombinations(unittest.TestCase):
    """E. 非法组合必须在构造 Job 之前报错。"""

    INVALID = {
        "未知格式": dict(fmt="xml"),
        "json 配分隔符": dict(fmt="json", delimiter="|"),
        "压缩级别为0": dict(compress=True, compression_level=0),
        "压缩级别为10": dict(compress=True, compression_level=10),
        "未压缩却设级别": dict(compress=False, compression_level=5),
        "重试间隔非正": dict(retry_count=3, retry_interval=0.0),
        "零重试却设间隔": dict(retry_count=0, retry_interval=2.0),
        "timeout 非正": dict(timeout=0),
        "batch_size 非正": dict(batch_size=0),
        "max_file_size 非正": dict(max_file_size_mb=0),
        "非法日志级别": dict(log_level="TRACE"),
        "要通知无邮箱": dict(notify_on_failure=True, notify_email=None),
        "有邮箱不通知": dict(notify_on_failure=False, notify_email="a@b.c"),
        "dry_run 带 schedule": dict(dry_run=True, schedule="0 0 * * *"),
        "dry_run 带 overwrite": dict(dry_run=True, overwrite=True),
        "max_workers 为 0": dict(max_workers=0),
        "buffer 过小": dict(buffer_size=1023),
        "空 name": dict(name=""),
        "空 source": dict(source=""),
    }

    def test_invalid_config_raises_before_job_construction(self):
        for label, overrides in self.INVALID.items():
            with self.subTest(case=label):
                with self.assertRaises(ConfigError):
                    ExportConfig(**{**REQUIRED, **overrides})

    def test_no_job_created_for_invalid_config(self):
        """显式证明：报错发生在 ExportJob 构造函数之外（配置先失败）。"""
        entered = []

        class Probe(ExportJob):
            def __init__(self, config, *args, **kwargs):
                entered.append(True)
                super().__init__(config, *args, **kwargs)

        with self.assertRaises(ConfigError):
            Probe(ExportConfig(**{**REQUIRED, "fmt": "xml"}))
        self.assertEqual(entered, [])  # Job 构造函数从未执行

    def test_invalid_through_legacy_form(self):
        """旧调用形式同样被前置校验拦截，且 Job 不被构造。"""
        with self.assertRaises(ConfigError):
            ExportJob("t", "/s", "/d", "xml")  # 第 4 个位置参数是 fmt
        with self.assertRaises(ConfigError):
            ExportJob("t", "/s", "/d", "csv", ",", "utf-8", False, 5)

    def test_unknown_parameter_rejected(self):
        with self.assertRaises(TypeError):
            ExportConfig.from_legacy("t", "/s", "/d", bogus=1)
        with self.assertRaises(TypeError):
            ExportConfig.from_legacy(*(["x"] * 26))  # 超出 25 个


class BoundaryValues(unittest.TestCase):
    """F. 合法边界值通过，越界值被拒。"""

    def test_compression_level_bounds(self):
        ExportConfig(**{**REQUIRED, "compress": True, "compression_level": 1})
        ExportConfig(**{**REQUIRED, "compress": True, "compression_level": 9})
        for bad in (-1, 0, 10, 11):
            with self.subTest(level=bad):
                with self.assertRaises(ConfigError):
                    ExportConfig(**{**REQUIRED, "compress": True,
                                    "compression_level": bad})

    def test_zero_retry_allowed_with_default_interval(self):
        cfg = ExportConfig(**{**REQUIRED, "retry_count": 0})
        self.assertEqual(ExportJob(cfg).plan()["retry"], (0, 0.0))

    def test_buffer_and_workers_bounds(self):
        ExportConfig(**{**REQUIRED, "buffer_size": 1024, "max_workers": 1})
        with self.assertRaises(ConfigError):
            ExportConfig(**{**REQUIRED, "buffer_size": 1023})
        with self.assertRaises(ConfigError):
            ExportConfig(**{**REQUIRED, "max_workers": -1})

    def test_json_only_with_default_delimiter(self):
        cfg = ExportConfig(**{**REQUIRED, "fmt": "json"})
        self.assertEqual(ExportJob(cfg).plan()["format"],
                         ("json", None, "utf-8"))


# 供"旧调用形式"回归使用：与 callsites.py 中 before() 完全相同的位置参数。
_POSITIONAL = {
    "site1 只传必填项": ("daily-sales", "/data/sales", "/out/sales"),
    "site2 全部 25 项": (
        "full-audit", "/data/audit", "/out/audit",
        "tsv", "\t", "utf-16", True, 9, 5, 2.5, 60.0, 500, 2048,
        False, False, "DEBUG", "ops@example.com", True, "0 3 * * *",
        True, False, False, "/scratch", 16, 65536),
    "site3 深层单点覆盖 dry_run": (
        "nightly-preview", "/data/nightly", "/out/nightly",
        "csv", ",", "utf-8", False, 6, 3, 1.0, 30.0, 1000, 100,
        True, True, "INFO", None, False, None, False, True),
    "site4 压缩+通知组合": (
        "monthly-finance", "/data/fin", "/out/fin",
        "csv", ",", "utf-8", True, 9, 3, 1.0, 30.0, 1000, 100,
        True, True, "INFO", "fin-ops@example.com", True),
    "site5 JSON+并发调优": (
        "api-dump", "/data/api", "/out/api",
        "json", ",", "utf-8", False, 6, 0, 1.0, 120.0, 2000, 512,
        False, True, "WARNING", None, False, None, True,
        False, True, None, 8, 16384),
}
_FIELD_NAMES = [
    "name", "source", "dest", "fmt", "delimiter", "encoding", "compress",
    "compression_level", "retry_count", "retry_interval", "timeout",
    "batch_size", "max_file_size_mb", "include_header", "skip_empty",
    "log_level", "notify_email", "notify_on_failure", "schedule",
    "overwrite", "dry_run", "checksum", "temp_dir", "max_workers",
    "buffer_size",
]


def _positional_args_for(label):
    return _POSITIONAL[label]


if __name__ == "__main__":
    unittest.main(verbosity=2)
