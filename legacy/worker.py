"""后台任务执行器（重构前）。"""

MAX_RETRIES = 3
JOB_TIMEOUT_SECONDS = 300


def run_job(fail_times=0):
    attempts = 0
    while True:
        attempts += 1
        if attempts > fail_times:
            return {"ok": True, "attempts": attempts}
        if attempts > MAX_RETRIES:
            return {"ok": False, "attempts": attempts}


def job_timed_out(elapsed_seconds):
    return elapsed_seconds > JOB_TIMEOUT_SECONDS

