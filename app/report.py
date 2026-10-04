"""报表生成（重构后：阈值来自 app.thresholds）。"""

from app.thresholds import HTTP_REQUEST_TIMEOUT_SECONDS, INGEST_BATCH_SIZE


def build_report(row_count):
    chunks = []
    remaining = row_count
    while remaining > 0:
        size = min(remaining, INGEST_BATCH_SIZE)
        chunks.append(size)
        remaining -= size
    return {"chunks": chunks, "timeout": HTTP_REQUEST_TIMEOUT_SECONDS}

