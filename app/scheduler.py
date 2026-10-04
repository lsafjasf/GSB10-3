"""入库批处理调度（重构后：阈值来自 app.thresholds）。"""

from app.thresholds import INGEST_BATCH_SIZE, JOB_TIMEOUT_SECONDS


def plan_batches(total):
    batches = []
    remaining = total
    while remaining > 0:
        size = min(remaining, INGEST_BATCH_SIZE)
        batches.append(size)
        remaining -= size
    return batches


def batch_timeout():
    return JOB_TIMEOUT_SECONDS

