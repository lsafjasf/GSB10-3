"""重构前：25 个位置参数的构造函数（问题现场）。

问题：
- 调用方必须按位置填参数，想改第 21 个参数就得把前 20 个默认值全抄一遍；
- 新增参数会改变位置含义，误伤所有调用点；
- 没有任何参数合法性校验，非法组合要到运行期才暴露。
"""

from job_behavior import describe as _describe, plan as _plan


class LegacyReportJob:
    def __init__(
        self,
        name,
        source_dsn,
        output_dir,
        batch_size=500,
        max_retries=3,
        retry_backoff_seconds=2.0,
        timeout_seconds=30.0,
        concurrency=4,
        enable_compression=True,
        compression_level=6,
        output_format="csv",
        delimiter=",",
        include_header=True,
        encoding="utf-8",
        buffer_size=65536,
        log_level="INFO",
        log_file=None,
        notify_email=None,
        schedule_cron=None,
        dry_run=False,
        overwrite=False,
        checkpoint_interval=1000,
        max_memory_mb=512,
        temp_dir=None,
        verify_checksum=True,
    ):
        self.name = name
        self.source_dsn = source_dsn
        self.output_dir = output_dir
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.timeout_seconds = timeout_seconds
        self.concurrency = concurrency
        self.enable_compression = enable_compression
        self.compression_level = compression_level
        self.output_format = output_format
        self.delimiter = delimiter
        self.include_header = include_header
        self.encoding = encoding
        self.buffer_size = buffer_size
        self.log_level = log_level
        self.log_file = log_file
        self.notify_email = notify_email
        self.schedule_cron = schedule_cron
        self.dry_run = dry_run
        self.overwrite = overwrite
        self.checkpoint_interval = checkpoint_interval
        self.max_memory_mb = max_memory_mb
        self.temp_dir = temp_dir
        self.verify_checksum = verify_checksum

    def describe(self):
        return _describe(self)

    def plan(self, total_rows):
        return _plan(self, total_rows)
