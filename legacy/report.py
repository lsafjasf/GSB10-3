"""报表生成（重构前）。"""

REQUEST_TIMEOUT = 30
MAX_BATCH_SIZE = 500


def build_report(row_count):
    chunks = []
    remaining = row_count
    while remaining > 0:
        size = min(remaining, MAX_BATCH_SIZE)
        chunks.append(size)
        remaining -= size
    return {"chunks": chunks, "timeout": REQUEST_TIMEOUT}

