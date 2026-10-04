"""演示脚本：隔离、拒绝计数、借用与收回（python3 demo.py）。"""

import threading
import time

from bulkhead_pool import (
    BulkheadThreadPool,
    CategoryConfig,
    RejectedExecutionError,
)

T0 = time.monotonic()


def ts():
    return f"{time.monotonic() - T0:5.2f}s"


def blocker(release):
    release.wait(5)
    return "done"


def main():
    pool = BulkheadThreadPool(
        {
            "fast": CategoryConfig(max_concurrency=2, max_queue=2),
            "slow": CategoryConfig(max_concurrency=2, max_queue=2),
        }
    )

    print("== 1. 慢业务打满自己的配额被拒绝，快业务不受影响 ==")
    fast_release = threading.Event()
    slow_release = threading.Event()
    for _ in range(2):
        pool.submit("fast", blocker, fast_release)  # fast 占满自己的舱位
    for _ in range(2):
        pool.submit("slow", blocker, slow_release)  # slow 占满自己的舱位
    for _ in range(2):
        pool.submit("slow", lambda: "queued")  # slow 排队到上限
    try:
        pool.submit("slow", lambda: "overflow")
    except RejectedExecutionError as e:
        print(f"[{ts()}] slow 第 5 个任务被拒绝: {e}")
    pool.submit("fast", lambda: "fast queued")  # fast 的队列额度不受影响
    s_fast, s_slow = pool.stats("fast"), pool.stats("slow")
    print(
        f"[{ts()}] 拒绝计数 slow={s_slow.rejected} fast={s_fast.rejected}"
        f"（各自独立统计）"
    )
    fast_release.set()
    slow_release.set()
    time.sleep(0.3)

    print("\n== 2. 空闲舱位借用与原属类别收回 ==")
    borrow_release = threading.Event()
    for _ in range(4):  # slow 占满自己 2 个 + 借用 fast 的 2 个
        pool.submit("slow", blocker, borrow_release)
    time.sleep(0.1)
    s = pool.stats("slow")
    print(f"[{ts()}] slow active={s.active} borrowed={s.borrowed}（借用了 fast 的舱位）")

    fast_gate = threading.Event()
    fast_started = threading.Event()

    def fast_task():
        fast_started.set()
        fast_gate.wait(5)

    pool.submit("fast", fast_task)  # 原属类别有需求，排队等待收回
    print(f"[{ts()}] fast 提交任务，等待收回舱位...")
    borrow_release.set()  # slow 的借用任务结束 -> 舱位收回给 fast
    fast_started.wait(5)
    print(f"[{ts()}] fast 已收回舱位并开始执行")
    fast_gate.set()
    time.sleep(0.2)

    print("\n== 3. 最终指标 ==")
    for name, s in pool.all_stats().items():
        print(
            f"  {name}: submitted={s.submitted} rejected={s.rejected} "
            f"completed={s.completed} failed={s.failed} "
            f"active={s.active} queued={s.queued}"
        )
    print(f"  全局空闲舱位: {pool.total_available_slots()}/{pool.total_slots}")
    pool.shutdown()


if __name__ == "__main__":
    main()
