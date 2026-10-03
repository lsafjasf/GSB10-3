"""错误汇总样例：多个清理函数抛错时，全部收集后一起报告。

运行：python3 demo_errors.py
"""

import asyncio

from cancel_scope import CancelScope, CleanupError


async def main():
    async with CancelScope() as scope:
        scope.on_cleanup(lambda: print("释放资源 1"))

        def fail_db():
            raise RuntimeError("数据库连接关闭失败: connection lost")

        scope.on_cleanup(fail_db)
        scope.on_cleanup(lambda: print("释放资源 3"))

        async def fail_lock():
            await asyncio.sleep(0)
            raise OSError("分布式锁释放超时")

        scope.on_cleanup(fail_lock)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except CleanupError as e:
        print("清理阶段共收集到 %d 个错误：" % len(e.errors))
        for i, err in enumerate(e.errors, 1):
            print("  %d) %s: %s" % (i, type(err).__name__, err))
