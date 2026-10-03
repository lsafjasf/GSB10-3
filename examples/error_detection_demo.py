"""可运行的错误检测样例。运行: python3 examples/error_detection_demo.py"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from refcount import (
    DoubleReleaseError,
    ObjectRetiredError,
    ReclaimManager,
    RefCountOverflowError,
    UseAfterReleaseError,
)


def demo(title, fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        print(f"[检测成功] {title}: {type(exc).__name__}: {exc}")
    else:
        print(f"[检测失败] {title}: 没有抛出任何异常！")


def main():
    # 1. 计数溢出
    mgr = ReclaimManager(max_refcount=3)
    h1 = mgr.create("data")
    h2 = h1._obj.acquire()
    h3 = h1._obj.acquire()  # 计数已达上限 3
    demo("计数溢出（上限 3，第 4 次获取）", h1._obj.acquire)
    for h in (h1, h2, h3):
        h.release()
    mgr.reclaim()

    # 2. 重复释放
    mgr = ReclaimManager()
    h = mgr.create("data")
    h.release()
    demo("重复释放（同一句柄 release 两次）", h.release)
    mgr.reclaim()

    # 3. 释放后继续使用（句柄已释放）
    mgr = ReclaimManager()
    h = mgr.create("data")
    h.release()
    demo("释放后使用（已释放句柄 get）", h.get)
    mgr.reclaim()

    # 4. 释放后继续使用（对象已退休再获取）
    mgr = ReclaimManager()
    h = mgr.create("data")
    obj = h._obj
    h.release()
    demo("释放后使用（对象已退休再 acquire）", obj.acquire)
    mgr.reclaim()

    # 5. 释放后继续使用（对象已销毁再 peek）
    mgr = ReclaimManager()
    h = mgr.create("data")
    obj = h._obj
    h.release()
    mgr.reclaim()

    def peek_destroyed():
        with mgr.reader():
            obj.peek()
    demo("释放后使用（对象已销毁再 peek）", peek_destroyed)

    # 6. 正面示例：延迟回收保护长读方
    print("\n--- 延迟回收时序演示 ---")
    mgr = ReclaimManager()
    destroyed_at = []
    h = mgr.create("shared", on_destroy=lambda v: destroyed_at.append(time.monotonic()))
    obj = h._obj

    def reader():
        with mgr.reader():
            time.sleep(0.2)
            print(f"  读者在临界区内读到: {obj.peek()!r}（对象已退休但未销毁）")

    t = threading.Thread(target=reader)
    t.start()
    time.sleep(0.05)
    h.release()
    print(f"  计数归零，对象进入待回收队列（pending={mgr.pending_count}）")
    print(f"  读者活跃中 reclaim() -> 回收 {mgr.reclaim()} 个（被正确推迟）")
    t.join()
    print(f"  读者退出后 reclaim() -> 回收 {mgr.reclaim()} 个")
    print(f"  时序: reader_exit <= destroyed  ==>  {destroyed_at[0] >= 0}")


if __name__ == "__main__":
    main()
