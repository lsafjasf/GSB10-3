"""重构后的 5 个调用点（配置对象 + 关键字，默认值无需再抄）。"""

from report_job import ReportJob, ReportJobConfig


def nightly_orders_export():
    # 只传必填项，其余默认
    return ReportJob(
        ReportJobConfig(
            name="nightly_orders",
            source_dsn="postgres://db/orders",
            output_dir="/data/out",
        )
    )


def inventory_snapshot():
    return ReportJob(
        ReportJobConfig(
            name="inventory_snapshot",
            source_dsn="mysql://db/inventory",
            output_dir="/data/out",
            batch_size=1000,
            max_retries=5,
            retry_backoff_seconds=1.5,
            timeout_seconds=60.0,
            concurrency=8,
        )
    )


def customer_dump_parquet():
    # 只关心要改的字段，中间一大片默认值消失
    return ReportJob(
        ReportJobConfig(
            name="customer_dump",
            source_dsn="postgres://db/customers",
            output_dir="/data/out",
            batch_size=2000,
            timeout_seconds=120.0,
            compression_level=9,
            output_format="parquet",
            include_header=False,
            checkpoint_interval=4000,
        )
    )


def audit_log_archive():
    return ReportJob(
        ReportJobConfig(
            name="audit_log_archive",
            source_dsn="postgres://db/audit",
            output_dir="/data/out",
            batch_size=250,
            log_level="WARNING",
            log_file="/var/log/audit.log",
            notify_email="ops@example.com",
            schedule_cron="0 2 * * *",
            overwrite=True,
        )
    )


def full_tunable_export():
    # 全部参数显式指定（与旧调用点逐字段一致）
    return ReportJob(
        ReportJobConfig(
            name="full_tunable_export",
            source_dsn="postgres://db/metrics",
            output_dir="/data/out",
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
            log_file="/var/log/full.log",
            notify_email="data@example.com",
            schedule_cron=None,
            dry_run=False,
            overwrite=True,
            checkpoint_interval=1200,
            max_memory_mb=1024,
            temp_dir="/tmp/export",
            verify_checksum=False,
        )
    )


ALL_CALL_SITES = (
    nightly_orders_export,
    inventory_snapshot,
    customer_dump_parquet,
    audit_log_archive,
    full_tunable_export,
)
