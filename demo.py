"""错误汇总样例：多个清理函数抛错时，错误被收集后一起报告。

运行：python3 demo.py
"""

import asyncio

from cancel_scope import CancelScope


async def main() -> None:
    acquired = []

    def make_resource(name, fail=False):
        acquired.append(name)
        print(f"[acquire ] {name}")

        def release():
            print(f"[release ] {name}")
            acquired.remove(name)
            if fail:
                raise RuntimeError(f"release {name} failed: handle busy")

        return release

    try:
        async with CancelScope() as scope:
            scope.defer(make_resource("db-connection"))
            scope.defer(make_resource("file-handle", fail=True))
            scope.defer(make_resource("cache-client", fail=True))
            scope.defer(make_resource("metrics"))
            print("[cancel  ] 中途取消，开始按逆序清理 ...")
            scope.cancel()
            await asyncio.sleep(10)
    except* RuntimeError as group:
        print(f"\n=== 错误汇总（{len(group.exceptions)} 个清理错误一起报告）===")
        for i, err in enumerate(group.exceptions, 1):
            print(f"  {i}. {type(err).__name__}: {err}")

    assert not acquired, f"资源泄漏: {acquired}"
    print("\n所有资源均已释放，无泄漏。")


if __name__ == "__main__":
    asyncio.run(main())
