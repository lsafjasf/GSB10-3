"""重构前（冻结快照，作为回归基线）。

ExportJob 有 25 个参数，调用方必须按位置传参；
想改靠后的参数，只能把前面一堆默认值原样抄上。
注意：旧代码不做任何参数校验，非法组合要到运行时才暴露。
"""


class ExportJob:
    def __init__(self, name, source, dest, fmt="csv", delimiter=",",
                 encoding="utf-8", compress=False, compression_level=6,
                 retry_count=3, retry_interval=1.0, timeout=30.0,
                 batch_size=1000, max_file_size_mb=100, include_header=True,
                 skip_empty=True, log_level="INFO", notify_email=None,
                 notify_on_failure=False, schedule=None, overwrite=False,
                 dry_run=False, checksum=True, temp_dir=None, max_workers=4,
                 buffer_size=8192):
        self.name = name
        self.source = source
        self.dest = dest
        self.fmt = fmt
        self.delimiter = delimiter
        self.encoding = encoding
        self.compress = compress
        self.compression_level = compression_level
        self.retry_count = retry_count
        self.retry_interval = retry_interval
        self.timeout = timeout
        self.batch_size = batch_size
        self.max_file_size_mb = max_file_size_mb
        self.include_header = include_header
        self.skip_empty = skip_empty
        self.log_level = log_level
        self.notify_email = notify_email
        self.notify_on_failure = notify_on_failure
        self.schedule = schedule
        self.overwrite = overwrite
        self.dry_run = dry_run
        self.checksum = checksum
        self.temp_dir = temp_dir
        self.max_workers = max_workers
        self.buffer_size = buffer_size

    def plan(self):
        """由参数推导出的执行计划（行为快照，用于回归对比）。"""
        return {
            "name": self.name,
            "io": (self.source, self.dest, self.temp_dir or "/tmp"),
            "format": (self.fmt,
                       self.delimiter if self.fmt in ("csv", "tsv") else None,
                       self.encoding),
            "header": self.include_header and self.fmt in ("csv", "tsv"),
            "skip_empty": self.skip_empty,
            "compression": (self.compress,
                            self.compression_level if self.compress else None),
            "retry": (self.retry_count,
                      self.retry_interval if self.retry_count > 0 else 0.0),
            "timeout": self.timeout,
            "batch_size": self.batch_size,
            "max_file_size_mb": self.max_file_size_mb,
            "log_level": self.log_level,
            "notify": self.notify_email if self.notify_on_failure else None,
            "schedule": None if self.dry_run else self.schedule,
            "mode": ("dry-run" if self.dry_run
                     else "overwrite" if self.overwrite
                     else "fail-if-exists"),
            "checksum": self.checksum,
            "workers": self.max_workers,
            "buffer_size": self.buffer_size,
        }
