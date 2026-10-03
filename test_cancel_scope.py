"""CancelScope 自测：python3 -m unittest test_cancel_scope -v"""

import asyncio
import unittest

from cancel_scope import CancelScope


class TestNormalExit(unittest.IsolatedAsyncioTestCase):
    async def test_cleanups_run_in_reverse_order_of_acquire(self):
        order = []
        async with CancelScope() as scope:
            scope.defer(lambda: order.append("release-A"))
            scope.defer(lambda: order.append("release-B"))

            async def release_c():
                await asyncio.sleep(0)
                order.append("release-C")

            scope.defer(release_c)
            order.append("body")
        self.assertEqual(order, ["body", "release-C", "release-B", "release-A"])

    async def test_use_pairs_acquire_and_release(self):
        events = []

        async def acquire(name):
            events.append(f"acquire-{name}")
            return name

        def release(name):
            events.append(f"release-{name}")

        async with CancelScope() as scope:
            await scope.use(lambda: acquire("db"), release)
            await scope.use(lambda: acquire("conn"), release)
        self.assertEqual(
            events,
            ["acquire-db", "acquire-conn", "release-conn", "release-db"],
        )

    async def test_empty_scope_exits_cleanly(self):
        async with CancelScope():
            pass


class TestCancellation(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_midway_still_runs_all_cleanups_lifo(self):
        order = []
        with self.assertRaises(asyncio.CancelledError):
            async with CancelScope() as scope:
                scope.defer(lambda: order.append("release-1"))
                scope.defer(lambda: order.append("release-2"))
                scope.cancel()
                await asyncio.sleep(10)
                order.append("unreachable")
        self.assertEqual(order, ["release-2", "release-1"])

    async def test_cancel_is_idempotent(self):
        order = []
        with self.assertRaises(asyncio.CancelledError):
            async with CancelScope() as scope:
                scope.defer(lambda: order.append("cleanup"))
                scope.cancel()
                scope.cancel()  # 重复取消不得重复打断/重复清理
                await asyncio.sleep(10)
        self.assertEqual(order, ["cleanup"])

    async def test_cancel_before_enter(self):
        order = []
        scope = CancelScope()
        scope.cancel()
        with self.assertRaises(asyncio.CancelledError):
            async with scope:
                scope.defer(lambda: order.append("cleanup"))
                await asyncio.sleep(10)
        self.assertEqual(order, ["cleanup"])


class TestCleanupErrors(unittest.IsolatedAsyncioTestCase):
    async def test_failing_cleanup_does_not_stop_others_and_errors_grouped(self):
        order = []

        def boom(name):
            def _cleanup():
                order.append(name)
                raise ValueError(f"{name} failed")

            return _cleanup

        with self.assertRaises(ExceptionGroup) as ctx:
            async with CancelScope() as scope:
                scope.defer(boom("first"))
                scope.defer(lambda: order.append("middle-ok"))
                scope.defer(boom("last"))
        # 逆序全部执行，抛错的清理没有中断后续清理
        self.assertEqual(order, ["last", "middle-ok", "first"])
        group = ctx.exception
        self.assertEqual(len(group.exceptions), 2)
        self.assertEqual(
            [str(e) for e in group.exceptions],
            ["last failed", "first failed"],
        )
        self.assertTrue(all(isinstance(e, ValueError) for e in group.exceptions))

    async def test_cleanup_error_during_cancel_is_still_reported(self):
        def bad():
            raise RuntimeError("cleanup blew up")

        with self.assertRaises(ExceptionGroup) as ctx:
            async with CancelScope() as scope:
                scope.defer(bad)
                scope.cancel()
                await asyncio.sleep(10)
        self.assertEqual(str(ctx.exception.exceptions[0]), "cleanup blew up")


class TestNestedScopes(unittest.IsolatedAsyncioTestCase):
    async def test_parent_waits_for_child_cleanup_before_its_own(self):
        events = []
        with self.assertRaises(asyncio.CancelledError):
            async with CancelScope() as parent:
                parent.defer(lambda: events.append("parent-cleanup"))
                async with parent.child() as child:
                    child.defer(lambda: events.append("child-cleanup"))
                    parent.cancel()
                    await asyncio.sleep(10)
        # 子作用域先清理，父作用域后清理
        self.assertEqual(events, ["child-cleanup", "parent-cleanup"])

    async def test_cancel_propagates_to_spawned_subtask_and_parent_waits(self):
        events = []
        started = asyncio.Event()

        async def worker(child):
            child.defer(lambda: events.append("worker-fast-cleanup"))

            async def slow_cleanup():
                await asyncio.sleep(0.05)
                events.append("worker-slow-cleanup-done")

            child.defer(slow_cleanup)
            started.set()
            await asyncio.sleep(10)

        with self.assertRaises(asyncio.CancelledError):
            async with CancelScope() as parent:
                parent.defer(lambda: events.append("parent-cleanup"))
                parent.start_soon(worker)
                await started.wait()
                parent.cancel()
                await asyncio.sleep(10)
        # 子任务内 LIFO：slow 先（后登记），fast 后；父的清理最后 => 父确实等了子
        self.assertEqual(
            events,
            ["worker-slow-cleanup-done", "worker-fast-cleanup", "parent-cleanup"],
        )

    async def test_child_entered_after_parent_cancelled_is_cancelled(self):
        events = []
        with self.assertRaises(asyncio.CancelledError):
            async with CancelScope() as parent:
                parent.cancel()
                async with parent.child() as child:
                    child.defer(lambda: events.append("late-child-cleanup"))
                    await asyncio.sleep(10)
        self.assertEqual(events, ["late-child-cleanup"])

    async def test_subtask_cleanup_error_is_collected_by_parent(self):
        async def worker(child):
            def bad():
                raise RuntimeError("worker cleanup failed")

            child.defer(bad)
            await asyncio.sleep(10)

        with self.assertRaises(ExceptionGroup) as ctx:
            async with CancelScope() as parent:
                parent.start_soon(worker)
                await asyncio.sleep(0.01)
                parent.cancel()
                await asyncio.sleep(10)
        # 父作用域的报告中嵌套了子作用域的清理错误
        child_group = ctx.exception.exceptions[0]
        self.assertIsInstance(child_group, ExceptionGroup)
        self.assertEqual(
            str(child_group.exceptions[0]), "worker cleanup failed"
        )


if __name__ == "__main__":
    unittest.main()
