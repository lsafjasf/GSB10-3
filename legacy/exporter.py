"""数据导出（重构前）。"""

MAX_BATCH_SIZE = 1000   # 导出分页大小
REQUEST_TIMEOUT = 45    # 导出下载超时


def export_pages(total):
    pages = []
    remaining = total
    while remaining > 0:
        size = min(remaining, MAX_BATCH_SIZE)
        pages.append(size)
        remaining -= size
    return pages


def download_timeout():
    return REQUEST_TIMEOUT

