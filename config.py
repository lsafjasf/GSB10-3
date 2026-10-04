"""重构后：带默认值的配置对象 + 前置校验。

ExportConfig 与旧构造函数的参数、默认值一一对应。
所有"参数组合合法性"校验在 __post_init__ 中完成：
非法组合在创建配置时就抛 ConfigError，ExportJob 根本不会被构造。
"""
from dataclasses import dataclass, fields
from typing import Optional

FORMATS = ("csv", "tsv", "json")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class ConfigError(ValueError):
    """非法参数（组合），在构造 ExportJob 之前抛出。"""


@dataclass
class ExportConfig:
    # ---- 必填 ----
    name: str
    source: str
    dest: str
    # ---- 可选（默认值与旧构造函数完全一致）----
    fmt: str = "csv"
    delimiter: str = ","
    encoding: str = "utf-8"
    compress: bool = False
    compression_level: int = 6
    retry_count: int = 3
    retry_interval: float = 1.0
    timeout: float = 30.0
    batch_size: int = 1000
    max_file_size_mb: int = 100
    include_header: bool = True
    skip_empty: bool = True
    log_level: str = "INFO"
    notify_email: Optional[str] = None
    notify_on_failure: bool = False
    schedule: Optional[str] = None
    overwrite: bool = False
    dry_run: bool = False
    checksum: bool = True
    temp_dir: Optional[str] = None
    max_workers: int = 4
    buffer_size: int = 8192

    def __post_init__(self):
        # 校验前置：配置对象诞生即合法，之后构造 Job 无需再查。
        self.validate()

    def validate(self):
        if not self.name:
            raise ConfigError("name 不能为空")
        if not self.source or not self.dest:
            raise ConfigError("source/dest 不能为空")
        if self.fmt not in FORMATS:
            raise ConfigError(f"fmt 必须是 {FORMATS} 之一，得到 {self.fmt!r}")
        if self.fmt == "json" and self.delimiter != ",":
            raise ConfigError("fmt='json' 时 delimiter 无效（仅 csv/tsv 使用分隔符）")
        if self.compress and not 1 <= self.compression_level <= 9:
            raise ConfigError("compress=True 时 compression_level 必须在 1..9")
        if not self.compress and self.compression_level != 6:
            raise ConfigError("compress=False 时设置 compression_level 无意义")
        if self.retry_count < 0:
            raise ConfigError("retry_count 不能为负")
        if self.retry_count > 0 and self.retry_interval <= 0:
            raise ConfigError("retry_count>0 时 retry_interval 必须为正数")
        if self.retry_count == 0 and self.retry_interval != 1.0:
            raise ConfigError("retry_count=0 时设置 retry_interval 无意义")
        if self.timeout <= 0:
            raise ConfigError("timeout 必须为正数")
        if self.batch_size <= 0:
            raise ConfigError("batch_size 必须为正数")
        if self.max_file_size_mb <= 0:
            raise ConfigError("max_file_size_mb 必须为正数")
        if self.log_level not in LOG_LEVELS:
            raise ConfigError(f"log_level 必须是 {LOG_LEVELS} 之一")
        if self.notify_on_failure and not self.notify_email:
            raise ConfigError("notify_on_failure=True 时必须提供 notify_email")
        if self.notify_email and not self.notify_on_failure:
            raise ConfigError("提供了 notify_email 但 notify_on_failure=False")
        if self.dry_run and self.schedule is not None:
            raise ConfigError("dry_run 模式下 schedule 无效")
        if self.dry_run and self.overwrite:
            raise ConfigError("dry_run 模式下 overwrite 无效")
        if self.max_workers < 1:
            raise ConfigError("max_workers 必须 >= 1")
        if self.buffer_size < 1024:
            raise ConfigError("buffer_size 必须 >= 1024")
        return self

    @classmethod
    def from_legacy(cls, *args, **kwargs):
        """把旧的"25 个位置参数"调用形式映射为配置对象（兼容层）。"""
        names = [f.name for f in fields(cls)]
        if len(args) > len(names):
            raise TypeError(
                f"旧调用形式最多 {len(names)} 个位置参数，得到 {len(args)} 个")
        values = dict(zip(names, args))
        for key, value in kwargs.items():
            if key in values:
                raise TypeError(f"参数 {key!r} 被重复赋值")
            if key not in names:
                raise TypeError(f"未知参数 {key!r}")
            values[key] = value
        return cls(**values)
