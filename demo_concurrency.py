"""并发事务观察演示：打印不同快照在同一时刻读到的数据差异。

运行：python3 demo_concurrency.py
"""

import threading
import time

from snapshot_mvcc import MVCCStore


def main():
    store = MVCCStore()
    commit_id = store.begin()
    commit_id.write("balance", 100)
    commit_id.commit()  # commit 1

    long_txn = store.begin()          # 长事务快照 A：可见集合 {1}
    barrier = threading.Barrier(2)
    log = []

    def short_writer():
        barrier.wait()
        for i in range(2, 5):         # 短事务连续提交 200/300/400
            txn = store.begin()
            txn.write("balance", i * 100)
            cid = txn.commit()
            log.append(f"短事务提交 commit={cid} balance={i * 100}")
            time.sleep(0.01)

    t = threading.Thread(target=short_writer)
    t.start()
    barrier.wait()



    mid_txn = None
    while t.is_alive():
        time.sleep(0.015)
        if mid_txn is None and store._next_commit_id >= 3:
            mid_txn = store.begin()   # 快照 B：在 commit 3 之后建立
    t.join()

    fresh_txn = store.begin()         # 快照 C：最新

    print("事件流水：")
    for line in log:
        print(" ", line)
    print()
    print(f"{'快照':<12}{'可见提交集合':<16}{'读到 balance'}")
    for name, txn in (("A(长事务)", long_txn), ("B(中途)", mid_txn), ("C(最新)", fresh_txn)):
        print(
            f"{name:<12}{str(sorted(txn.snapshot.visible_commits)):<16}"
            f"{txn.read('balance')}"
        )
    assert long_txn.read("balance") == 100
    assert fresh_txn.read("balance") == 400
    print("\n结论：同一时刻三个快照读到三个不同版本，互不影响。")


if __name__ == "__main__":
    main()
