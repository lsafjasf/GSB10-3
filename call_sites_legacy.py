"""重构前的 5 个调用点（位置参数形式，原样保留作为回归基准）。

痛点示例：customer_dump_parquet 只想改第 22 个参数 checkpoint_interval，
却被迫把 14~21 位的默认值全部照抄一遍。
"""

from legacy_report_job import LegacyReportJob

# 每个调用点的位置参数（回归测试会复用这份数据验证旧调用形式兼容）
ARGS = {
    # 调用点 1：只传必填项，其余全用默认值
    "nightly_orders_export": (
        "nightly_orders",
        "postgres://db/orders",
        "/data/out",
    ),
    # 调用点 2：必填 + 前 5 个可选项
    "inventory_snapshot": (
        "inventory_snapshot",
        "mysql://db/inventory",
        "/data/out",
        1000,   # batch_size
        5,      # max_retries
        1.5,    # retry_backoff_seconds
        60.0,   # timeout_seconds
        8,      # concurrency
    ),
    # 调用点 3：想改 checkpoint_interval（第 22 位），被迫抄 14~21 位默认值
    "customer_dump_parquet": (
        "customer_dump",
        "postgres://db/customers",
        "/data/out",
        2000,       # batch_size
        3,          # max_retries
        2.0,        # retry_backoff_seconds
        120.0,      # timeout_seconds
        4,          # concurrency
        True,       # enable_compression
        9,          # compression_level
        "parquet",  # output_format
        ",",        # delimiter
        False,      # include_header
        "utf-8",    # encoding
        65536,      # buffer_size
        "INFO",     # log_level
        None,       # log_file
        None,       # notify_email
        None,       # schedule_cron
        False,      # dry_run
        False,      # overwrite
        4000,       # checkpoint_interval（必须是 batch_size 的整数倍）
    ),
    # 调用点 4：调度归档任务，覆盖日志/通知/调度等中间区段参数
    "audit_log_archive": (
        "audit_log_archive",
        "postgres://db/audit",
        "/data/out",
        250,                    # batch_size
        3,                      # max_retries
        2.0,                    # retry_backoff_seconds
        30.0,                   # timeout_seconds
        4,                      # concurrency
        True,                   # enable_compression
        6,                      # compression_level
        "csv",                  # output_format
        ",",                    # delimiter
        True,                   # include_header
        "utf-8",                # encoding
        65536,                  # buffer_size
        "WARNING",              # log_level
        "/var/log/audit.log",   # log_file
        "ops@example.com",      # notify_email
        "0 2 * * *",            # schedule_cron
        False,                  # dry_run
        True,                   # overwrite
    ),
    # 调用点 5：25 个参数全部显式指定
    "full_tunable_export": (
        "full_tunable_export",
        "postgres://db/metrics",
        "/data/out",
        400,            # batch_size
        2,              # max_retries
        5.0,            # retry_backoff_seconds
        300.0,          # timeout_seconds
        16,             # concurrency
        False,          # enable_compression
        0,              # compression_level（关闭压缩时必须为 0）
        "json",         # output_format
        ",",            # delimiter（非 csv 不允许自定义）
        False,          # include_header
        "utf-16",       # encoding
        8192,           # buffer_size
        "DEBUG",        # log_level
        "/var/log/full.log",  # log_file（DEBUG 必须指定）
        "data@example.com",   # notify_email
        None,           # schedule_cron
        False,          # dry_run
        True,           # overwrite
        1200,           # checkpoint_interval（400 的整数倍）
        1024,           # max_memory_mb
        "/tmp/export",  # temp_dir
        False,          # verify_checksum
    ),
}


def nightly_orders_export():
    return LegacyReportJob(*ARGS["nightly_orders_export"])


def inventory_snapshot():
    return LegacyReportJob(*ARGS["inventory_snapshot"])


def customer_dump_parquet():
    return LegacyReportJob(*ARGS["customer_dump_parquet"])


def audit_log_archive():
    return LegacyReportJob(*ARGS["audit_log_archive"])


def full_tunable_export():
    return LegacyReportJob(*ARGS["full_tunable_export"])


ALL_CALL_SITES = (
    nightly_orders_export,
    inventory_snapshot,
    customer_dump_parquet,
    audit_log_archive,
    full_tunable_export,
)
