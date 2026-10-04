"""数据导出（重构后：阈值来自 app.thresholds）。"""

from app.thresholds import EXPORT_BATCH_SIZE, EXPORT_REQUEST_TIMEOUT_SECONDS


def export_pages(total):
    pages = []
    remaining = total
    while remaining > 0:
        size = min(remaining, EXPORT_BATCH_SIZE)
        pages.append(size)
        remaining -= size
    return pages


def download_timeout():
    return EXPORT_REQUEST_TIMEOUT_SECONDS

