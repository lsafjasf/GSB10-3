"""回归测试：重构前后行为一致性 + 校验前置 + 边界用例。

运行：python3 -m unittest test_report_job -v
"""

import unittest

import call_sites_legacy
import call_sites_new
from job_behavior import describe, plan
from legacy_report_job import LegacyReportJob
from report_job import ReportJob, ReportJobConfig

ROW_COUNTS = (0, 1, 999, 10_000, 1_000_001)


class CallSiteRegressionTests(unittest.TestCase):
    """逐个调用点：旧类(位置参数) vs 新类(配置对象) 行为必须完全一致。"""

    def test_all_call_sites_behavior_identical(self):
        for legacy_factory, new_factory in zip(
            call_sites_legacy.ALL_CALL_SITES, call_sites_new.ALL_CALL_SITES
        ):
            site = legacy_factory.__name__
            with self.subTest(call_site=site):
                legacy_job = legacy_factory()
                new_job = new_factory()
                self.assertEqual(legacy_job.describe(), new_job.describe())
                for rows in ROW_COUNTS:
                    self.assertEqual(
                        legacy_job.plan(rows),
                        new_job.plan(rows),
                        msg="%s: plan(%d) 不一致" % (site, rows),
                    )

    def test_every_legacy_call_site_is_covered(self):
        legacy_names = [f.__name__ for f in call_sites_legacy.ALL_CALL_SITES]
        new_names = [f.__name__ for f in call_sites_new.ALL_CALL_SITES]
        self.assertEqual(legacy_names, new_names)
        self.assertEqual(len(legacy_names), 5)


class OldCallFormCompatTests(unittest.TestCase):
    """旧调用形式：ReportJob(*位置参数) 与 LegacyReportJob 行为一致。"""

    def test_positional_form_matches_legacy(self):
        for site, args in call_sites_legacy.ARGS.items():
            with self.subTest(call_site=site):
                legacy_job = LegacyReportJob(*args)
                compat_job = ReportJob(*args)
                self.assertEqual(legacy_job.describe(), compat_job.describe())
                for rows in ROW_COUNTS:
                    self.assertEqual(legacy_job.plan(rows), compat_job.plan(rows))

    def test_positional_form_matches_new_keyword_form(self):
        for site, args in call_sites_legacy.ARGS.items():
            with self.subTest(call_site=site):
                compat_job = ReportJob(*args)
                new_job = getattr(call_sites_new, site)()
                self.assertEqual(compat_job.describe(), new_job.describe())

    def test_positional_form_is_validated_too(self):
        # 旧形式同样先过校验：batch_size=0 必须在构造前报错
        with self.assertRaises(ValueError):
            ReportJob("bad", "dsn", "/out", 0)

    def test_too_many_positional_args_rejected(self):
        with self.assertRaises(TypeError):
            ReportJob(*("x",) * 26)

    def test_config_plus_extra_args_rejected(self):
        config = ReportJobConfig(name="a", source_dsn="b", output_dir="c")
        with self.assertRaises(TypeError):
            ReportJob(config, 123)
        with self.assertRaises(TypeError):
            ReportJob(config, batch_size=10)


class RequiredOnlyTests(unittest.TestCase):
    """边界：只传必填项，默认值全部生效。"""

    def test_required_only(self):
        job = ReportJob(
            ReportJobConfig(name="minimal", source_dsn="sqlite://x", output_dir="/tmp/o")
        )
        self.assertEqual(job.config.batch_size, 500)
        self.assertEqual(job.config.max_retries, 3)
        self.assertEqual(job.config.output_format, "csv")
        self.assertTrue(job.config.verify_checksum)
        self.assertIn("batch_size=500", job.describe())

    def test_missing_required_rejected(self):
        with self.assertRaises(TypeError):
            ReportJobConfig(name="x", source_dsn="y")  # 缺 output_dir


class AllParamsTests(unittest.TestCase):
    """边界：25 个参数全部显式传入。"""

    def test_all_params(self):
        config = ReportJobConfig(
            name="full",
            source_dsn="dsn",
            output_dir="/out",
            batch_size=400,
            max_retries=2,
            retry_backoff_seconds=5.0,
            timeout_seconds=300.0,
            concurrency=16,
            enable_compression=False,
            compression_level=0,
            output_format="json",
            delimiter=",",
            include_header=False,
            encoding="utf-16",
            buffer_size=8192,
            log_level="DEBUG",
            log_file="/var/log/f.log",
            notify_email="a@b.c",
            schedule_cron=None,
            dry_run=False,
            overwrite=True,
            checkpoint_interval=1200,
            max_memory_mb=1024,
            temp_dir="/tmp/e",
            verify_checksum=False,
        )
        job = ReportJob(config)
        self.assertEqual(job.config.buffer_size, 8192)
        self.assertIn("compression=off", job.describe())
        self.assertEqual(job.plan(10_000)["write_mode"], "overwrite")


class IllegalCombinationTests(unittest.TestCase):
    """边界：非法组合必须在构造 ReportJob 之前（即构造 Config 时）报错。"""

    BASE = dict(name="t", source_dsn="dsn", output_dir="/out")

    ILLEGAL_CASES = {
        "空 name": dict(name="  "),
        "batch_size 为 0": dict(batch_size=0),
        "重试次数为负": dict(max_retries=-1),
        "重试>0 但退避为 0": dict(max_retries=1, retry_backoff_seconds=0),
        "超时非正数": dict(timeout_seconds=0),
        "并发超上限": dict(concurrency=65),
        "开压缩但级别越界": dict(enable_compression=True, compression_level=10),
        "关压缩但级别非 0": dict(enable_compression=False, compression_level=6),
        "非法输出格式": dict(output_format="xml"),
        "json 自定义分隔符": dict(output_format="json", delimiter=";"),
        "分隔符非单字符": dict(delimiter="||"),
        "非法编码": dict(encoding="gbk"),
        "buffer 过小": dict(buffer_size=1024),
        "非法日志级别": dict(log_level="TRACE"),
        "DEBUG 无日志文件": dict(log_level="DEBUG", log_file=None),
        "dry_run 带调度": dict(dry_run=True, schedule_cron="0 0 * * *"),
        "checkpoint 非 batch 整数倍": dict(batch_size=500, checkpoint_interval=1200),
        "内存不足下限": dict(max_memory_mb=32),
        "并发*批量超内存保护": dict(concurrency=64, batch_size=2000),
    }

    def test_each_illegal_combination_rejected_before_construction(self):
        for label, overrides in self.ILLEGAL_CASES.items():
            with self.subTest(case=label):
                kwargs = dict(self.BASE, **overrides)
                with self.assertRaises(ValueError):
                    ReportJobConfig(**kwargs)

    def test_error_message_reports_all_problems(self):
        kwargs = dict(self.BASE, batch_size=0, concurrency=0, log_level="NOPE")
        with self.assertRaises(ValueError) as ctx:
            ReportJobConfig(**kwargs)
        message = str(ctx.exception)
        self.assertIn("batch_size", message)
        self.assertIn("concurrency", message)
        self.assertIn("log_level", message)

    def test_illegal_config_never_reaches_job(self):
        # 校验在 Config 阶段抛出，ReportJob 根本没机会被构造
        with self.assertRaises(ValueError):
            ReportJob(ReportJobConfig(**self.BASE, dry_run=True, schedule_cron="* * * * *"))


class BoundaryValueTests(unittest.TestCase):
    """边界值：合法组合的临界点必须仍然可用。"""

    BASE = dict(name="t", source_dsn="dsn", output_dir="/out")

    def test_valid_boundary_values(self):
        valid_cases = [
            dict(batch_size=1, checkpoint_interval=1),
            dict(concurrency=1),
            dict(concurrency=64),
            dict(max_retries=0, retry_backoff_seconds=0),  # 不重试时退避可为 0
            dict(enable_compression=False, compression_level=0),
            dict(compression_level=1),
            dict(compression_level=9),
            dict(buffer_size=4096),
            dict(max_memory_mb=64),
            dict(concurrency=50, batch_size=2000, checkpoint_interval=2000),  # 恰好 100_000 上限
            dict(log_level="DEBUG", log_file="/tmp/d.log"),
        ]
        for overrides in valid_cases:
            with self.subTest(case=overrides):
                job = ReportJob(ReportJobConfig(**dict(self.BASE, **overrides)))
                self.assertIsNotNone(job.describe())

    def test_plan_rejects_negative_rows(self):
        job = ReportJob(ReportJobConfig(**self.BASE))
        with self.assertRaises(ValueError):
            job.plan(-1)

    def test_plan_zero_rows(self):
        job = ReportJob(ReportJobConfig(**self.BASE))
        result = job.plan(0)
        self.assertEqual(result["batches"], 0)
        self.assertEqual(result["effective_concurrency"], 0)


if __name__ == "__main__":
    unittest.main()
