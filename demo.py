"""Quick demo: isolation, rejection counts, borrow and reclaim."""

import threading
import time

from bulkhead_pool import BulkheadPool, RejectedError


def timed(label, fn):
    start = time.monotonic()
    fn()
    print(f"  [{label}] elapsed {time.monotonic() - start:.2f}s")


def main():
    pool = BulkheadPool(total_slots=3)
    pool.register_category("fast", reserved=2, queue_limit=2)
    pool.register_category("slow", reserved=1, queue_limit=2)

    print("1) 'slow' borrows idle slots and saturates the pool:")
    gate = threading.Event()

    def slow_task(i):
        gate.wait(timeout=5.0)
        return i

    slow_futs = [pool.submit("slow", slow_task, i) for i in range(4)]  # 3 run, 1 queued
    print("   slow stats:", pool.stats("slow"))

    print("2) 'fast' tasks are queued; slow queue is full -> rejected:")
    fast_futs = []
    for i in range(4):
        try:
            fast_futs.append(pool.submit("fast", slow_task, f"f{i}"))
        except RejectedError:
            pass
    print("   fast stats:", pool.stats("fast"), " rejected:", pool.stats("fast").rejected)

    print("3) releasing slow tasks -> fast reclaims its reserved slots:")
    gate.set()
    results = sorted((f.result(timeout=5) for f in slow_futs + fast_futs), key=str)
    print("   completed:", results)
    print("   slow:", pool.stats("slow"), "\n   fast:", pool.stats("fast"))
    print("   free slots:", pool.free_slots())
    pool.shutdown()


if __name__ == "__main__":
    main()
