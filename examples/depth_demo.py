"""重入深度样例: 逐步打印 read/write/subsumed_read 深度与全局状态。

运行: python3 examples/depth_demo.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reentrant_rwlock import (  # noqa: E402
    LockUpgradeError,
    ReentrantRWLock,
)


def show(lock, step):
    snap = lock.snapshot()
    print(f"{step:42s} read={snap['read']} write={snap['write']} "
          f"subsumed={snap['subsumed_read']} "
          f"全局写深度={snap['global_write_depth']} "
          f"全局读者数={snap['global_reader_count']} "
          f"is_writer={snap['is_writer']}")


def main():
    print("== 读锁重入: 深度逐层归零才真正释放 ==")
    lock = ReentrantRWLock()
    show(lock, "初始")
    lock.acquire_read(); show(lock, "acquire_read #1")
    lock.acquire_read(); show(lock, "acquire_read #2(重入)")
    lock.release_read(); show(lock, "release_read (深度 2->1, 未释放)")
    lock.release_read(); show(lock, "release_read (深度 1->0, 真正释放)")

    print("\n== 写锁重入 ==")
    lock.acquire_write(); show(lock, "acquire_write #1")
    lock.acquire_write(); show(lock, "acquire_write #2(重入)")
    lock.release_write(); show(lock, "release_write (深度 2->1, 仍互斥)")
    lock.release_write(); show(lock, "release_write (深度 1->0, 真正释放)")

    print("\n== 写持有期间取读 = 并入(SUBSUMED_READ), 非降级 ==")
    lock.acquire_write(); show(lock, "acquire_write")
    lock.acquire_read(); show(lock, "acquire_read(写期间, 保持互斥)")
    lock.release_read(); show(lock, "release_read(并入帧, 写锁不受影响)")
    lock.release_write(); show(lock, "release_write(真正释放)")

    print("\n== 显式升级: 唯一单层读者 -> 写者 ==")
    lock.acquire_read(); show(lock, "acquire_read")
    lock.upgrade(); show(lock, "upgrade(读帧原子变写帧)")
    lock.release_write(); show(lock, "release_write")

    print("\n== 显式降级: 写者 -> 读者 ==")
    lock.acquire_write(); show(lock, "acquire_write")
    lock.downgrade(); show(lock, "downgrade(交出互斥, 成为读者)")
    lock.release_read(); show(lock, "release_read(真正释放)")

    print("\n== 不支持的组合立即报错, 不阻塞 ==")
    lock.acquire_read()
    try:
        lock.acquire_write()
    except LockUpgradeError as exc:
        print(f"  读中直接 acquire_write -> LockUpgradeError: {exc}")
    lock.acquire_read()
    try:
        lock.upgrade()
    except LockUpgradeError as exc:
        print(f"  读重入状态 upgrade -> LockUpgradeError: {exc}")
    lock.release_read()
    lock.release_read()
    show(lock, "收尾, 状态归零")


if __name__ == "__main__":
    main()
