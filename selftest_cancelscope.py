#!/usr/bin/env python3
"""cancelscope 自测：虚拟时钟驱动，零真实等待，结果确定性。

运行：
    python3 selftest_cancelscope.py          # 全部用例（虚拟时钟，瞬时完成）
    python3 selftest_cancelscope.py --demo   # 附加一个真实时钟的演示

覆盖：正常完成 / 父超时级联取消 / 子失败提前结束（两种策略）/
      取消后仍在收尾的任务 / 嵌套级联 / 外部取消 / 边界用例。
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
import traceback
from typing import Optional, Type

from cancelscope import (
    FailurePolicy,
    RealClock,
    Scope,
    ScopeTimeout,
    VirtualClock,
    active_count,
)

# ---------------------------------------------------------------------------
# 测试工具
# ---------------------------------------------------------------------------

REPORTS = []  # 活动任务数对比数据


def report(scenario: str, before: int, during: Optional[int], after: int) -> None:
    REPORTS.append((scenario, before, during, after))


@contextlib.asynccontextmanager
async def expect(exc_type: Type[BaseException]):
    """断言代码块抛出指定异常；未抛或抛错类型都记为失败。"""
    try:
        yield
    except exc_type:
        return
    raise AssertionError(f"期望抛出 {exc_type.__name__}，但未抛出")


class Worker:
    """可观测的测试工人：记录取消信号与收尾完成情况。

    - suppress_cancel=True：收到取消信号后吞掉异常，把收尾做完再正常结束
      （模拟“取消后仍在收尾”的任务）；
    - 否则收尾完成后重新抛出 CancelledError。
    """

    def __init__(self, clock: VirtualClock, name: str, work: float,
                 *, cleanup: float = 0.0, fail_at: Optional[float] = None,
                 suppress_cancel: bool = False) -> None:
        self.clock = clock
        self.name = name
        self.work = work
        self.cleanup = cleanup
        self.fail_at = fail_at
        self.suppress_cancel = suppress_cancel
        self.cancelled = False        # 是否收到取消信号
        self.cleanup_done = False     # 收尾是否完成

    async def run(self) -> str:
        try:
            if self.fail_at is not None:
                await self.clock.sleep(self.fail_at)
                raise RuntimeError(f"{self.name} 在 t={self.clock.now():.0f}s 失败")
            await self.clock.sleep(self.work)
            return f"{self.name}-ok"
        except asyncio.CancelledError:
            self.cancelled = True
            if self.cleanup > 0:
                await self.clock.sleep(self.cleanup)  # 收尾：释放资源
            self.cleanup_done = True
            if self.suppress_cancel:
                return f"{self.name}-cleaned"
            raise


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------

async def test_normal_completion() -> None:
    """正常完成：全部子任务在超时前结束，无取消信号，结果完整。"""
    clock = VirtualClock()
    check(active_count() == 0, "前置：全局活动任务应为 0")
    before = None
    async with Scope(timeout=10.0, clock=clock, name="normal") as scope:
        workers = [Worker(clock, f"w{i}", work=2.0 + i) for i in range(3)]
        tasks = [scope.spawn(w.run(), name=w.name) for w in workers]
        await clock.settle()
        before = active_count()
        await clock.advance(5.0)  # 最慢任务 t=4 完成，t=10 超时不应触发
    results = [t.result() for t in tasks]
    check(results == [f"w{i}-ok" for i in range(3)], f"结果不完整: {results}")
    check(all(not w.cancelled for w in workers), "正常完成不应有取消信号")
    signalled = sum(1 for s in scope.children if s.cancel_signalled)
    check(signalled == 0, "正常完成不应有取消信号")
    check(active_count() == 0, "退出后全局活动任务应为 0")
    report("正常完成", before=before, during=None, after=active_count())


async def test_parent_timeout_cascades() -> None:
    """父超时：所有子任务收到取消信号，未取消子任务数为 0，资源全部释放。"""
    clock = VirtualClock()
    workers = [Worker(clock, f"w{i}", work=100.0, cleanup=5.0) for i in range(5)]
    before = during = None
    async with expect(ScopeTimeout):
        async with Scope(timeout=10.0, clock=clock, name="timeout") as scope:
            for w in workers:
                scope.spawn(w.run(), name=w.name)
            await clock.settle()
            before = active_count()
            await clock.advance(10.0)          # 触发超时 → 级联取消
            during = active_count()            # 子任务正在收尾（cleanup 中）
            await clock.advance(5.0)           # 收尾完成
    after = active_count()
    report("父超时级联取消", before=before, during=during, after=after)
    check(before == 5, f"超时前应有 5 个活动任务，实际 {before}")
    check(during == 5, f"取消后收尾中应仍有 5 个活动任务，实际 {during}")
    check(after == 0, f"退出后活动任务应为 0，实际 {after}")
    check(all(w.cancelled for w in workers), "存在未收到取消信号的子任务")
    check(all(w.cleanup_done for w in workers), "存在未完成收尾的子任务")
    check(scope.uncancelled_children == 0,
          f"未取消子任务数必须为 0，实际 {scope.uncancelled_children}")


async def test_child_failure_fail_fast() -> None:
    """子失败（FAIL_FAST）：父按策略提前结束，其余子任务被级联取消。"""
    clock = VirtualClock()
    bad = Worker(clock, "bad", work=0, fail_at=3.0)
    others = [Worker(clock, f"w{i}", work=100.0, cleanup=1.0) for i in range(3)]
    t_exit = None
    during = None
    async with expect(RuntimeError):
        async with Scope(timeout=60.0, clock=clock, policy=FailurePolicy.FAIL_FAST,
                         name="fail-fast") as scope:
            scope.spawn(bad.run(), name="bad")
            for w in others:
                scope.spawn(w.run(), name=w.name)
            await clock.advance(3.0)           # bad 失败 → 级联取消
            await clock.settle()
            t_exit = clock.now()
            during = active_count()
            check(during == 3, "兄弟任务应在收尾中")
            await clock.advance(1.0)           # 收尾完成
    check(t_exit == 3.0, f"应在 t=3s 提前结束，实际 t={t_exit}s（远早于 60s 超时）")
    check(all(w.cancelled for w in others), "兄弟任务应收到取消信号")
    signalled = [s for s in scope.children if s.name != "bad"]
    check(all(s.cancel_signalled for s in signalled), "兄弟任务应收到取消信号")
    check(scope.children[0].error is not None, "失败任务应记录原始错误")
    check(active_count() == 0, "退出后活动任务应为 0")
    report("子失败-FAIL_FAST", before=4, during=during, after=active_count())


async def test_child_failure_wait_all() -> None:
    """子失败（WAIT_ALL）：不打扰兄弟任务，全部结束后抛出首个失败。"""
    clock = VirtualClock()
    bad = Worker(clock, "bad", work=0, fail_at=3.0)
    others = [Worker(clock, f"w{i}", work=8.0) for i in range(2)]
    before = None
    t_exit = None
    async with expect(RuntimeError):
        async with Scope(timeout=60.0, clock=clock, policy=FailurePolicy.WAIT_ALL,
                         name="wait-all") as scope:
            scope.spawn(bad.run(), name="bad")
            for w in others:
                scope.spawn(w.run(), name=w.name)
            await clock.settle()
            before = active_count()
            await clock.advance(8.0)   # 兄弟任务 t=8 全部结束
            t_exit = clock.now()
            check(all(s.done for s in scope.children), "WAIT_ALL 应等全部结束")
    check(t_exit == 8.0, f"应等全部结束（t=8s）再抛错，实际 t={t_exit}s")
    check(all(not w.cancelled for w in others), "WAIT_ALL 不应取消兄弟任务")
    check(active_count() == 0, "退出后活动任务应为 0")
    report("子失败-WAIT_ALL", before=before, during=None, after=active_count())


async def test_cleanup_after_cancel() -> None:
    """取消后仍在收尾：吞掉取消信号做收尾的任务，退出前必须等它收尾完。"""
    clock = VirtualClock()
    slow = [Worker(clock, f"slow{i}", work=100.0, cleanup=4.0,
                   suppress_cancel=True) for i in range(2)]
    fast = [Worker(clock, f"fast{i}", work=100.0, cleanup=1.0) for i in range(2)]
    during = None
    async with expect(ScopeTimeout):
        async with Scope(timeout=5.0, clock=clock, name="cleanup") as scope:
            for w in slow + fast:
                scope.spawn(w.run(), name=w.name)
            await clock.advance(5.0)           # 超时 → 级联取消
            await clock.advance(1.0)           # fast 收尾完，slow 仍在收尾
            check(active_count() == 2, "slow 任务应仍在收尾")
            during = active_count()
            await clock.advance(3.0)           # slow 收尾完
    check(all(w.cleanup_done for w in slow + fast), "所有收尾必须完成")
    check(scope.uncancelled_children == 0, "未取消子任务数必须为 0")
    check(active_count() == 0, "退出后活动任务应为 0（收尾未泄漏）")
    report("取消后仍在收尾", before=4, during=during, after=active_count())


async def test_nested_cascade() -> None:
    """嵌套作用域：根超时 → 父取消 → 孙取消，三层全部收到信号。"""
    clock = VirtualClock()
    grandchildren = []

    async def parent(idx: int) -> None:
        async with Scope(timeout=None, clock=clock, name=f"child{idx}") as inner:
            for j in range(2):
                g = Worker(clock, f"g{idx}{j}", work=100.0, cleanup=1.0)
                grandchildren.append(g)
                inner.spawn(g.run(), name=g.name)
            await inner.wait()

    async with expect(ScopeTimeout):
        async with Scope(timeout=10.0, clock=clock, name="root") as root:
            for i in range(2):
                root.spawn(parent(i), name=f"child{i}")
            await clock.advance(10.0)
            await clock.advance(1.0)
    report("嵌套级联取消", before=6, during=None, after=active_count())
    check(len(grandchildren) == 4, "应派生 4 个孙任务")
    check(all(g.cancelled for g in grandchildren), "孙任务必须全部收到取消信号")
    check(all(g.cleanup_done for g in grandchildren), "孙任务收尾必须完成")
    check(active_count() == 0, "退出后活动任务应为 0")


async def test_external_cancel() -> None:
    """外部取消父任务：级联取消子任务，CancelledError 如实向上传播。"""
    clock = VirtualClock()
    workers = [Worker(clock, f"w{i}", work=100.0, cleanup=1.0) for i in range(3)]
    holder = {}

    async def main() -> None:
        async with Scope(timeout=None, clock=clock, name="ext") as scope:
            holder["scope"] = scope
            for w in workers:
                scope.spawn(w.run(), name=w.name)
            await scope.wait()

    task = asyncio.ensure_future(main())
    await clock.settle()
    check(active_count() == 3, "取消前应有 3 个活动任务")
    task.cancel()
    await clock.settle()
    await clock.advance(1.0)
    await asyncio.sleep(0)
    check(task.cancelled(), "父任务应表现为被取消")
    check(all(w.cancelled for w in workers), "子任务必须全部收到取消信号")
    check(holder["scope"].uncancelled_children == 0, "未取消子任务数必须为 0")
    report("外部取消", before=3, during=None, after=active_count())
    check(active_count() == 0, "退出后活动任务应为 0")


async def test_wait_returns_after_cancel() -> None:
    """回归：子任务被级联取消后，scope.wait() 必须返回而不是挂死。"""
    clock = VirtualClock()

    async def body() -> None:
        async with expect(ScopeTimeout):
            async with Scope(timeout=5.0, clock=clock, name="wait") as scope:
                for i in range(3):
                    scope.spawn(Worker(clock, f"w{i}", work=100.0).run(), name=f"w{i}")
                await scope.wait()  # 超时取消后必须在此返回

    task = asyncio.ensure_future(body())
    await clock.settle()
    await clock.advance(5.0)       # 触发超时 → 级联取消
    await clock.settle()
    check(task.done(), "wait() 必须在子任务被取消后返回（回归：曾挂死）")
    await task
    report("wait解除阻塞", before=3, during=None, after=active_count())
    check(active_count() == 0, "退出后活动任务应为 0")


async def test_boundaries() -> None:
    """边界：同刻到期超时优先 / timeout=0 立即超时 / timeout=None 不超时 /
    关闭后 spawn 报错 / 多失败打包 ExceptionGroup。"""
    # 1) 子任务到期时刻 == 超时时刻 → 超时优先
    clock = VirtualClock()
    w = Worker(clock, "tie", work=5.0)
    async with expect(ScopeTimeout):
        async with Scope(timeout=5.0, clock=clock, name="tie") as scope:
            scope.spawn(w.run(), name="tie")
            await clock.advance(5.0)
    check(w.cancelled, "同刻到期应判定超时并取消子任务")
    check(scope.uncancelled_children == 0, "未取消子任务数必须为 0")

    # 2) timeout=0 → 立即超时
    clock = VirtualClock()
    async with expect(ScopeTimeout):
        async with Scope(timeout=0.0, clock=clock, name="zero") as scope:
            scope.spawn(Worker(clock, "w", work=1.0).run(), name="w")
            await clock.settle()
    check(scope.uncancelled_children == 0, "未取消子任务数必须为 0")

    # 3) timeout=None → 永不超时
    clock = VirtualClock()
    async with Scope(timeout=None, clock=clock, name="none") as scope:
        t = scope.spawn(Worker(clock, "w", work=1000.0).run(), name="w")
        await clock.advance(1000.0)
    check(t.result() == "w-ok", "timeout=None 应正常跑完")

    # 4) 关闭后 spawn → RuntimeError
    clock = VirtualClock()
    async with Scope(timeout=None, clock=clock, name="closed") as scope:
        pass
    late_coro = Worker(clock, "late", work=1.0).run()
    try:
        scope.spawn(late_coro)
    except RuntimeError:
        late_coro.close()  # 拒绝派生时回收协程，避免泄漏告警
        pass
    else:
        raise AssertionError("关闭后 spawn 应抛 RuntimeError")

    # 5) 多个子任务同时失败 → ExceptionGroup
    clock = VirtualClock()
    async with expect(ExceptionGroup):
        async with Scope(timeout=None, clock=clock, policy=FailurePolicy.WAIT_ALL,
                         name="multi") as scope:
            scope.spawn(Worker(clock, "f1", work=0, fail_at=1.0).run(), name="f1")
            scope.spawn(Worker(clock, "f2", work=0, fail_at=1.0).run(), name="f2")
            await clock.advance(1.0)
    check(active_count() == 0, "边界用例结束后活动任务应为 0")
    report("边界用例", before=0, during=None, after=active_count())


# ---------------------------------------------------------------------------
# 真实时钟演示（--demo）
# ---------------------------------------------------------------------------

async def demo_real_clock() -> None:
    clock = RealClock()
    t0 = time.monotonic()
    try:
        async with Scope(timeout=0.2, clock=clock, name="demo") as scope:
            for i in range(3):
                scope.spawn(asyncio.sleep(10), name=f"sleeper{i}")
            await scope.wait()
    except ScopeTimeout as exc:
        dt = time.monotonic() - t0
        print(f"[demo] 真实时钟: {exc}（耗时 {dt:.2f}s，活动任务 {active_count()}）")


# ---------------------------------------------------------------------------
# 测试运行器
# ---------------------------------------------------------------------------

TESTS = [
    ("正常完成", test_normal_completion),
    ("父超时级联取消", test_parent_timeout_cascades),
    ("子失败-FAIL_FAST", test_child_failure_fail_fast),
    ("子失败-WAIT_ALL", test_child_failure_wait_all),
    ("取消后仍在收尾", test_cleanup_after_cancel),
    ("嵌套级联取消", test_nested_cascade),
    ("外部取消父任务", test_external_cancel),
    ("wait解除阻塞", test_wait_returns_after_cancel),
    ("边界用例", test_boundaries),
]


def main() -> int:
    failures = 0
    print(f"Python {sys.version.split()[0]}  标准库: asyncio  时钟: VirtualClock（可注入）\n")
    for title, fn in TESTS:
        try:
            asyncio.run(fn())
        except Exception:
            failures += 1
            print(f"[FAIL] {title}")
            traceback.print_exc()
        else:
            print(f"[PASS] {title}")

    print("\n活动任务数对比（全局 active_count）：")
    print(f"  {'场景':<16}{'取消前':>6}{'收尾中':>6}{'退出后':>6}")
    for scenario, before, during, after in REPORTS:
        mid = "-" if during is None else str(during)
        print(f"  {scenario:<16}{before:>6}{mid:>6}{after:>6}")

    if "--demo" in sys.argv:
        asyncio.run(demo_real_clock())

    print(f"\n结果: {len(TESTS) - failures}/{len(TESTS)} 通过")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
