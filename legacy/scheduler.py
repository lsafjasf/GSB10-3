"""入库批处理调度（重构前）。"""

JOB_TIMEOUT_SECONDS = 300
MAX_BATCH_SIZE = 500


def plan_batches(total):
    batches = []
    remaining = total
    while remaining > 0:
        size = min(remaining, MAX_BATCH_SIZE)
        batches.append(size)
        remaining -= size
    return batches


def batch_timeout():
    return JOB_TIMEOUT_SECONDS

