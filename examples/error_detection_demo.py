"""错误检测样例：溢出、重复释放、释放后使用、退役后获取。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rcuref import (
    Domain,
    DoubleReleaseError,
    ObjectReclaimedError,
    RefcountOverflowError,
    RetiredObjectError,
    UseAfterReleaseError,
)


def show(title, fn):
    try:
        fn()
    except Exception as exc:
        print(f"[检出] {title}: {type(exc).__name__}: {exc}")
    else:
        print(f"[失败] {title}: 未报错！")


def main():
    dom = Domain()

    # 1. 计数溢出
    small = dom.register("x", max_refcount=2)
    small.acquire()
    show("计数溢出 (max_refcount=2, 第 3 次 acquire)",
         lambda: small.acquire())

    # 2. 重复释放（Ref）
    obj = dom.register("y")
    ref = obj.acquire()
    ref.release()
    show("重复释放同一个 Ref", lambda: ref.release())

    # 3. 重复释放（创建引用）
    obj2 = dom.register("z")
    obj2.release()
    show("重复释放创建引用", lambda: obj2.release())

    # 4. 释放后继续使用
    obj3 = dom.register("w")
    ref3 = obj3.acquire()
    ref3.release()
    show("释放后通过 Ref 访问数据", lambda: ref3.get())

    # 5. 退役后获取
    obj4 = dom.register("v")
    obj4.release()
    show("对已退役对象 acquire", lambda: obj4.acquire())

    # 6. 回收后访问
    dom.drain(timeout=5)
    show("回收后访问 payload", lambda: obj4.payload)

    # 7. 无配对的 reader_exit
    show("reader_exit 无配对 enter", lambda: dom.reader_exit())


if __name__ == "__main__":
    main()
