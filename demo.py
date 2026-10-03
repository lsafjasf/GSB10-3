"""重入深度样例：直观展示读写锁的持有者/深度记账与升降级规则。

运行：python3 demo.py
"""

from reentrant_rw_lock import (
    DowngradeError,
    ReentrantReadWriteLock,
    UpgradeError,
)


def show(lock, tag):
    readers, writer, wdepth = lock._snapshot()
    print(f"  [{tag}] 读者表={readers or '{}'} 写者={writer} 写深度={wdepth}")


def main():
    lock = ReentrantReadWriteLock()

    print("== 1. 读锁重入：深度 1 -> 2 -> 3，归零才真正释放 ==")
    lock.acquire_read(); show(lock, "第 1 次 acquire_read")
    lock.acquire_read(); show(lock, "第 2 次 acquire_read")
    lock.acquire_read(); show(lock, "第 3 次 acquire_read")
    lock.release_read(); show(lock, "release 后深度 2，仍持有")
    lock.release_read(); lock.release_read()
    show(lock, "深度归零，真正释放")

    print("\n== 2. 写锁重入：权限不降级 ==")
    lock.acquire_write(); show(lock, "第 1 次 acquire_write")
    lock.acquire_write(); show(lock, "第 2 次 acquire_write（重入）")

    print("\n== 3. 写者拿读锁：允许，独立记账，写锁原样保留 ==")
    lock.acquire_read(); show(lock, "写者 acquire_read")
    lock.release_read(); show(lock, "释放该读锁后写锁仍在")

    print("\n== 4. 显式降级：要求写深度为 1 ==")
    try:
        lock.downgrade()
    except DowngradeError as e:
        print(f"  写深度 2 时降级被拒绝: {e}")
    lock.release_write(); show(lock, "写深度回到 1")
    lock.downgrade(); show(lock, "downgrade() 成功：写->读")
    lock.release_read(); show(lock, "读锁也释放")

    print("\n== 5. 读->写升级：唯一读者允许，多读者报错 ==")
    lock.acquire_read()
    lock.acquire_write()  # 唯一读者，就地升级
    show(lock, "唯一读者升级成功")
    lock.release_write()
    lock.acquire_read()
    import threading
    t = threading.Thread(target=lambda: (lock.acquire_read(),
                                         threading.Event().wait(0.3),
                                         lock.release_read()))
    t.start()
    t.join(0.05)
    try:
        lock.acquire_write()
    except UpgradeError as e:
        print(f"  多读者时升级被拒绝: {e}")
    lock.release_read()
    t.join()


if __name__ == "__main__":
    main()
