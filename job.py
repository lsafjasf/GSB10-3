"""重构后：ExportJob 只接收一个 ExportConfig。

兼容：仍接受旧的 25 个位置参数形式（内部转成 ExportConfig，
校验同样前置——配置对象创建失败时 Job 不会被构造）。
"""
from config import ExportConfig


class ExportJob:
    def __init__(self, config, *args, **kwargs):
        if not isinstance(config, ExportConfig):
            # 旧调用形式：ExportJob("name", "src", "dst", "csv", ",", ...)
            config = ExportConfig.from_legacy(config, *args, **kwargs)
        self.config = config

    def plan(self):
        """与 legacy_job.ExportJob.plan 逐项等价（回归测试保证）。"""
        c = self.config
        return {
            "name": c.name,
            "io": (c.source, c.dest, c.temp_dir or "/tmp"),
            "format": (c.fmt,
                       c.delimiter if c.fmt in ("csv", "tsv") else None,
                       c.encoding),
            "header": c.include_header and c.fmt in ("csv", "tsv"),
            "skip_empty": c.skip_empty,
            "compression": (c.compress,
                            c.compression_level if c.compress else None),
            "retry": (c.retry_count,
                      c.retry_interval if c.retry_count > 0 else 0.0),
            "timeout": c.timeout,
            "batch_size": c.batch_size,
            "max_file_size_mb": c.max_file_size_mb,
            "log_level": c.log_level,
            "notify": c.notify_email if c.notify_on_failure else None,
            "schedule": None if c.dry_run else c.schedule,
            "mode": ("dry-run" if c.dry_run
                     else "overwrite" if c.overwrite
                     else "fail-if-exists"),
            "checksum": c.checksum,
            "workers": c.max_workers,
            "buffer_size": c.buffer_size,
        }
