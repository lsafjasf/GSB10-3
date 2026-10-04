"""重构后：配置对象 + 构造前校验。

- ReportJobConfig：带默认值的关键字配置对象，__post_init__ 中完成全部
  合法性校验（含参数组合校验），非法组合在构造 ReportJob 之前就报错；
- ReportJob：接收 ReportJobConfig；同时兼容旧的位置参数调用形式
  （内部先转成 Config 并校验），保证旧调用点行为不变。
"""

import dataclasses
from dataclasses import dataclass
from typing import Optional

from job_behavior import describe as _describe, plan as _plan

OUTPUT_FORMATS = ("csv", "json", "parquet")
ENCODINGS = ("utf-8", "utf-16", "latin-1")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
MAX_CONCURRENCY = 64
MIN_MEMORY_MB = 64
MAX_INFLIGHT_ROWS = 100_000  # concurrency * batch_size 的内存保护上限


@dataclass
class ReportJobConfig:
    # ---- 必填项（无默认值） ----
    name: str
    source_dsn: str
    output_dir: str
    # ---- 可选项（带默认值，新增参数只需在末尾加字段，不影响调用点） ----
    batch_size: int = 500
    max_retries: int = 3
    retry_backoff_seconds: float = 2.0
    timeout_seconds: float = 30.0
    concurrency: int = 4
    enable_compression: bool = True
    compression_level: int = 6
    output_format: str = "csv"
    delimiter: str = ","
    include_header: bool = True
    encoding: str = "utf-8"
    buffer_size: int = 65536
    log_level: str = "INFO"
    log_file: Optional[str] = None
    notify_email: Optional[str] = None
    schedule_cron: Optional[str] = None
    dry_run: bool = False
    overwrite: bool = False
    checkpoint_interval: int = 1000
    max_memory_mb: int = 512
    temp_dir: Optional[str] = None
    verify_checksum: bool = True

    def __post_init__(self):
        # 校验前置：配置对象一旦存在就必然合法，非法组合到不了 ReportJob。
        errors = self.validate()
        if errors:
            raise ValueError(
                "非法的 ReportJobConfig（%d 处问题）：\n- %s"
                % (len(errors), "\n- ".join(errors))
            )

    def validate(self):
        """返回全部校验错误（空列表表示合法）。先收集再统一抛出。"""
        errors = []
        # 单参数校验
        if not isinstance(self.name, str) or not self.name.strip():
            errors.append("name 必须是非空字符串")
        if not isinstance(self.source_dsn, str) or not self.source_dsn.strip():
            errors.append("source_dsn 必须是非空字符串")
        if not isinstance(self.output_dir, str) or not self.output_dir.strip():
            errors.append("output_dir 必须是非空字符串")
        if self.batch_size < 1:
            errors.append("batch_size 必须 >= 1，当前为 %r" % self.batch_size)
        if self.max_retries < 0:
            errors.append("max_retries 必须 >= 0，当前为 %r" % self.max_retries)
        if self.timeout_seconds <= 0:
            errors.append("timeout_seconds 必须 > 0，当前为 %r" % self.timeout_seconds)
        if not 1 <= self.concurrency <= MAX_CONCURRENCY:
            errors.append(
                "concurrency 必须在 1..%d，当前为 %r" % (MAX_CONCURRENCY, self.concurrency)
            )
        if self.output_format not in OUTPUT_FORMATS:
            errors.append(
                "output_format 必须是 %s 之一，当前为 %r" % (OUTPUT_FORMATS, self.output_format)
            )
        if len(self.delimiter) != 1:
            errors.append("delimiter 必须是单字符，当前为 %r" % self.delimiter)
        if self.encoding not in ENCODINGS:
            errors.append("encoding 必须是 %s 之一，当前为 %r" % (ENCODINGS, self.encoding))
        if self.buffer_size < 4096:
            errors.append("buffer_size 必须 >= 4096，当前为 %r" % self.buffer_size)
        if self.log_level not in LOG_LEVELS:
            errors.append("log_level 必须是 %s 之一，当前为 %r" % (LOG_LEVELS, self.log_level))
        if self.max_memory_mb < MIN_MEMORY_MB:
            errors.append("max_memory_mb 必须 >= %d，当前为 %r" % (MIN_MEMORY_MB, self.max_memory_mb))

        # 参数组合校验
        if self.max_retries > 0 and self.retry_backoff_seconds <= 0:
            errors.append(
                "组合非法：max_retries=%r 时 retry_backoff_seconds 必须 > 0，当前为 %r"
                % (self.max_retries, self.retry_backoff_seconds)
            )
        if self.enable_compression:
            if not 1 <= self.compression_level <= 9:
                errors.append(
                    "组合非法：启用压缩时 compression_level 必须在 1..9，当前为 %r"
                    % self.compression_level
                )
        elif self.compression_level != 0:
            errors.append(
                "组合非法：关闭压缩时 compression_level 必须为 0，当前为 %r"
                % self.compression_level
            )
        if self.output_format != "csv" and self.delimiter != ",":
            errors.append(
                "组合非法：delimiter 仅对 csv 有效，output_format=%r 时不能自定义 delimiter"
                % self.output_format
            )
        if self.log_level == "DEBUG" and not self.log_file:
            errors.append("组合非法：log_level=DEBUG 时必须指定 log_file")
        if self.dry_run and self.schedule_cron is not None:
            errors.append("组合非法：dry_run 任务不允许注册 schedule_cron")
        if self.batch_size >= 1 and self.checkpoint_interval % self.batch_size != 0:
            errors.append(
                "组合非法：checkpoint_interval(%r) 必须是 batch_size(%r) 的整数倍"
                % (self.checkpoint_interval, self.batch_size)
            )
        if self.batch_size >= 1 and self.concurrency * self.batch_size > MAX_INFLIGHT_ROWS:
            errors.append(
                "组合非法：concurrency(%r) * batch_size(%r) 超过内存保护上限 %d"
                % (self.concurrency, self.batch_size, MAX_INFLIGHT_ROWS)
            )
        return errors

    @classmethod
    def from_legacy(cls, *args, **kwargs):
        """把旧的位置参数形式映射为关键字配置（保持参数顺序不变）。"""
        field_names = [f.name for f in dataclasses.fields(cls)]
        if len(args) > len(field_names):
            raise TypeError(
                "旧调用形式最多接受 %d 个位置参数，实际传入 %d 个"
                % (len(field_names), len(args))
            )
        merged = dict(zip(field_names, args))
        for key, value in kwargs.items():
            if key in merged:
                raise TypeError("参数 %r 同时以位置和关键字形式传入" % key)
            if key not in field_names:
                raise TypeError("未知参数 %r" % key)
            merged[key] = value
        return cls(**merged)


class ReportJob:
    """重构后的任务类。

    新形式：ReportJob(ReportJobConfig(name=..., ...))
    旧形式：ReportJob("name", "dsn", "out", 500, ...)  —— 兼容保留，
            内部先转成 Config 并校验，行为与 LegacyReportJob 一致。
    """

    def __init__(self, config, *args, **kwargs):
        if isinstance(config, ReportJobConfig):
            if args or kwargs:
                raise TypeError("传入 ReportJobConfig 后不允许再带额外参数")
            self.config = config
        else:
            # 旧调用形式：第一个位置参数是 name
            self.config = ReportJobConfig.from_legacy(config, *args, **kwargs)

    def __getattr__(self, item):
        # 让 behavior 函数和外部读取方能像访问旧对象一样访问配置字段
        return getattr(self.config, item)

    def describe(self):
        return _describe(self.config)

    def plan(self, total_rows):
        return _plan(self.config, total_rows)
