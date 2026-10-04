"""5 个真实调用点：before = 旧位置参数形式，after = 新配置对象形式。

每个调用点的 after 版本只写出"与默认值不同"的参数，
新增参数时不再需要回头改这些调用点。
"""
from legacy_job import ExportJob as LegacyExportJob
from config import ExportConfig
from job import ExportJob


# ---- 调用点 1：只传必填项 ----
def site1_before():
    return LegacyExportJob("daily-sales", "/data/sales", "/out/sales")


def site1_after():
    return ExportJob(ExportConfig(
        name="daily-sales", source="/data/sales", dest="/out/sales"))


# ---- 调用点 2：25 个参数全部显式指定 ----
def site2_before():
    return LegacyExportJob(
        "full-audit", "/data/audit", "/out/audit",
        "tsv", "\t", "utf-16", True, 9, 5, 2.5, 60.0, 500, 2048,
        False, False, "DEBUG", "ops@example.com", True, "0 3 * * *",
        True, False, False, "/scratch", 16, 65536)


def site2_after():
    return ExportJob(ExportConfig(
        name="full-audit", source="/data/audit", dest="/out/audit",
        fmt="tsv", delimiter="\t", encoding="utf-16", compress=True,
        compression_level=9, retry_count=5, retry_interval=2.5,
        timeout=60.0, batch_size=500, max_file_size_mb=2048,
        include_header=False, skip_empty=False, log_level="DEBUG",
        notify_email="ops@example.com", notify_on_failure=True,
        schedule="0 3 * * *", overwrite=True, dry_run=False,
        checksum=False, temp_dir="/scratch", max_workers=16,
        buffer_size=65536))


# ---- 调用点 3：只想改第 21 个参数 dry_run，旧形式被迫抄 18 个默认值 ----
def site3_before():
    return LegacyExportJob(
        "nightly-preview", "/data/nightly", "/out/nightly",
        "csv", ",", "utf-8", False, 6, 3, 1.0, 30.0, 1000, 100,
        True, True, "INFO", None, False, None, False, True)


def site3_after():
    return ExportJob(ExportConfig(
        name="nightly-preview", source="/data/nightly",
        dest="/out/nightly", dry_run=True))


# ---- 调用点 4：压缩 + 失败通知组合 ----
def site4_before():
    return LegacyExportJob(
        "monthly-finance", "/data/fin", "/out/fin",
        "csv", ",", "utf-8", True, 9, 3, 1.0, 30.0, 1000, 100,
        True, True, "INFO", "fin-ops@example.com", True)


def site4_after():
    return ExportJob(ExportConfig(
        name="monthly-finance", source="/data/fin", dest="/out/fin",
        compress=True, compression_level=9,
        notify_email="fin-ops@example.com", notify_on_failure=True))


# ---- 调用点 5：JSON 导出 + 并发/缓冲调优 ----
def site5_before():
    return LegacyExportJob(
        "api-dump", "/data/api", "/out/api",
        "json", ",", "utf-8", False, 6, 0, 1.0, 120.0, 2000, 512,
        False, True, "WARNING", None, False, None, True,
        False, True, None, 8, 16384)


def site5_after():
    return ExportJob(ExportConfig(
        name="api-dump", source="/data/api", dest="/out/api",
        fmt="json", retry_count=0, timeout=120.0, batch_size=2000,
        max_file_size_mb=512, include_header=False, log_level="WARNING",
        overwrite=True, max_workers=8, buffer_size=16384))


CALLSITES = [
    ("site1 只传必填项", site1_before, site1_after),
    ("site2 全部 25 项", site2_before, site2_after),
    ("site3 深层单点覆盖 dry_run", site3_before, site3_after),
    ("site4 压缩+通知组合", site4_before, site4_after),
    ("site5 JSON+并发调优", site5_before, site5_after),
]
