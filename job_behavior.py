"""任务行为逻辑（纯函数）。

同时被重构前的 LegacyReportJob 和重构后的 ReportJob 复用，
保证两份实现的行为定义只有一份，回归对比才有意义。
"""

import math


def describe(settings):
    """返回任务生效配置的描述串。"""
    compression = (
        "off"
        if not settings.enable_compression
        else "on(level=%d)" % settings.compression_level
    )
    return (
        "ReportJob(name=%r, format=%s, batch_size=%d, concurrency=%d, "
        "compression=%s, encoding=%s, header=%s, "
        "retries=%d(backoff=%ss), timeout=%ss, buffer=%d, "
        "log=%s(%s), notify=%s, cron=%s, "
        "dry_run=%s, overwrite=%s, checkpoint=%d, max_mem=%dMB, "
        "temp=%s, checksum=%s)"
        % (
            settings.name,
            settings.output_format,
            settings.batch_size,
            settings.concurrency,
            compression,
            settings.encoding,
            settings.include_header,
            settings.max_retries,
            settings.retry_backoff_seconds,
            settings.timeout_seconds,
            settings.buffer_size,
            settings.log_level,
            settings.log_file or "stderr",
            settings.notify_email or "-",
            settings.schedule_cron or "-",
            settings.dry_run,
            settings.overwrite,
            settings.checkpoint_interval,
            settings.max_memory_mb,
            settings.temp_dir or "-",
            settings.verify_checksum,
        )
    )


def plan(settings, total_rows):
    """根据行数生成执行计划（纯函数，只依赖配置）。"""
    if total_rows < 0:
        raise ValueError("total_rows must be >= 0")
    batches = math.ceil(total_rows / settings.batch_size) if total_rows else 0
    effective_concurrency = min(settings.concurrency, batches)
    return {
        "name": settings.name,
        "total_rows": total_rows,
        "batches": batches,
        "checkpoints": total_rows // settings.checkpoint_interval,
        "effective_concurrency": effective_concurrency,
        "estimated_memory_mb": min(
            settings.max_memory_mb,
            64 + effective_concurrency * settings.batch_size // 128,
        ),
        "compressed_bytes_estimate": (
            None
            if not settings.enable_compression
            else total_rows * (10 - settings.compression_level)
        ),
        "write_mode": (
            "dry-run" if settings.dry_run else ("overwrite" if settings.overwrite else "append")
        ),
        "verify_checksum": settings.verify_checksum,
    }
