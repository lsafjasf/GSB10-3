"""CancelScope 自测：正常退出 / 中途取消 / 清理抛错 / 嵌套作用域 / 边界用例。

运行：python3 -m unittest -v
"""

import asyncio
import unittest

from cancel_scope import CancelScope, CleanupError, ScopeCancelled


class TestNormalExit(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_runs_in_reverse_order_of_registration(self):
        log = []
        async with CancelScope() as scope:
            scope.on_cleanup(lambda: log.append("cleanup-A"))
            scope.on_cleanup(lambda: log.append("cleanup-B"))
            scope.on_cleanup(lambda: log.append("cleanup-C"))
            log.append("body")
        # 申请顺序 A -> B -> C，清理必须是 C -> B -> A
        self.assertEqual(log, ["body", "cleanup-C", "cleanup-B", "cleanup-A"])
        self.assertFalse(scope.cancelled)

    async def test_async_cleanup_supported_and_ordered(self):
        log = []

        async def async_cleanup(name):
            await asyncio.sleep(0)
            log.append(name)

        async with CancelScope() as scope:
            scope.on_cleanup(lambda: async_cleanup("async-1"))
            scope.on_cleanup(lambda: log.append("sync-2"))
            scope.on_cleanup(lambda: async_cleanup("async-3"))
        self.assertEqual(log, ["async-3", "sync-2", "async-1"])

    async def test_body_exception_still_runs_cleanups_and_propagates(self):
        log = []
        with self.assertRaises(ValueError):
            async with CancelScope() as scope:
                scope.on_cleanup(lambda: log.append("cleanup"))
                raise ValueError("boom")
        self.assertEqual(log, ["cleanup"])


class TestMidCancel(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_interrupts_body_and_runs_cleanups(self):
        log = []
        entered = asyncio.Event()

        async def body():
            async with CancelScope() as scope:
                scope.on_cleanup(lambda: log.append("cleanup-1"))
                scope.on_cleanup(lambda: log.append("cleanup-2"))
                entered.set()
                await asyncio.Event().wait()  # 永不返回，直到被取消
            log.append("after-scope")  # 取消被吞掉，正常走到这里
            return scope

        task = asyncio.create_task(body())
        await entered.wait()
        await asyncio.sleep(0)
        # 从外部取消作用域
        # （task 里拿不到 scope 引用，用事件把 scope 传出来更符合真实用法）
        # 这里直接通过 checkpoint 测试协作式取消，另起一个用例测任务级取消。
        task.cancel()  # 外部取消：不应被吞掉
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(log, ["cleanup-2", "cleanup-1"])

    async def test_scope_cancel_suppresses_internal_cancellation(self):
        log = []
        holder = {}

        async def body():
            async with CancelScope() as scope:
                holder["scope"] = scope
                scope.on_cleanup(lambda: log.append("cleanup"))
                await asyncio.Event().wait()
            log.append("after-scope")

        task = asyncio.create_task(body())
        while "scope" not in holder:
            await asyncio.sleep(0)
        await asyncio.sleep(0)
        holder["scope"].cancel()
        await task  # 不抛异常：作用域自己的取消被 __aexit__ 吞掉
        self.assertEqual(log, ["cleanup", "after-scope"])
        self.assertTrue(holder["scope"].cancelled)

    async def test_checkpoint_raises_when_cancelled(self):
        scope = CancelScope()
        await scope.checkpoint()  # 未取消时正常通过
        scope.cancel()
        with self.assertRaises(ScopeCancelled):
            await scope.checkpoint()


class TestCleanupErrors(unittest.IsolatedAsyncioTestCase):
    async def test_errors_collected_and_do_not_stop_remaining_cleanups(self):
        log = []

        def boom(msg):
            raise RuntimeError(msg)

        scope = CancelScope()
        with self.assertRaises(CleanupError) as ctx:
            async with scope:
                scope.on_cleanup(lambda: log.append("cleanup-1"))
                scope.on_cleanup(lambda: boom("fail-2"))
                scope.on_cleanup(lambda: log.append("cleanup-3"))
                scope.on_cleanup(lambda: boom("fail-4"))

        err = ctx.exception
        # 两个失败都被收集，且成功的清理仍然执行，顺序仍是逆序
        self.assertEqual(len(err.errors), 2)
        self.assertEqual([str(e) for e in err.errors], ["fail-4", "fail-2"])
        self.assertEqual(log, ["cleanup-3", "cleanup-1"])
        # 错误汇总样例（README 中引用的输出）
        print("\n[sample] %s: %s" % (type(err).__name__, err))

    async def test_body_error_and_cleanup_error_are_chained(self):
        with self.assertRaises(CleanupError) as ctx:
            async with CancelScope() as scope:
                scope.on_cleanup(lambda: (_ for _ in ()).throw(KeyError("k")))
                raise ValueError("body-failed")
        self.assertIsInstance(ctx.exception.__cause__, ValueError)

    async def test_async_cleanup_error_also_collected(self):
        async def bad():
            await asyncio.sleep(0)
            raise OSError("async-fail")

        with self.assertRaises(CleanupError) as ctx:
            async with CancelScope() as scope:
                scope.on_cleanup(bad)
        self.assertEqual(len(ctx.exception.errors), 1)
        self.assertIsInstance(ctx.exception.errors[0], OSError)


class TestNestedScopes(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_propagates_and_parent_waits_for_child_cleanup(self):
        log = []
        child_entered = asyncio.Event()

        async def child_body(parent):
            async with parent.child() as child:
                child.on_cleanup(lambda: log.append("child-cleanup-1"))
                child.on_cleanup(lambda: log.append("child-cleanup-2"))
                child_entered.set()
                await asyncio.Event().wait()

        async with CancelScope() as parent:
            parent.on_cleanup(lambda: log.append("parent-cleanup"))
            task = asyncio.create_task(child_body(parent))
            await child_entered.wait()
            parent.cancel()
            await asyncio.Event().wait()  # 父作用域体在这里收到取消

        await task
        # 子作用域清理必须先于父作用域自身资源的清理
        self.assertEqual(
            log,
            ["child-cleanup-2", "child-cleanup-1", "parent-cleanup"],
        )

    async def test_parent_normal_exit_waits_for_child(self):
        log = []
        child_entered = asyncio.Event()

        async def child_body(parent):
            async with parent.child() as child:
                child.on_cleanup(lambda: log.append("child-cleanup"))
                child_entered.set()
                await asyncio.sleep(0.05)
                log.append("child-body-done")

        async with CancelScope() as parent:
            parent.on_cleanup(lambda: log.append("parent-cleanup"))
            task = asyncio.create_task(child_body(parent))
            await child_entered.wait()
            log.append("parent-body-done")
            # 父作用域体先结束，但 __aexit__ 必须等子作用域清理完

        await task
        self.assertEqual(
            log,
            ["parent-body-done", "child-body-done", "child-cleanup", "parent-cleanup"],
        )

    async def test_grandchild_cleanup_order(self):
        log = []

        async def grandchild_body(child_scope):
            async with child_scope.child() as gc:
                gc.on_cleanup(lambda: log.append("grandchild-cleanup"))
                await asyncio.Event().wait()

        async def child_body(parent):
            async with parent.child() as child:
                child.on_cleanup(lambda: log.append("child-cleanup"))
                task = asyncio.create_task(grandchild_body(child))
                await asyncio.Event().wait()
                await task

        async with CancelScope() as parent:
            parent.on_cleanup(lambda: log.append("parent-cleanup"))
            task = asyncio.create_task(child_body(parent))
            await asyncio.sleep(0.05)  # 让三代都进入
            parent.cancel()
            await asyncio.Event().wait()

        await task
        self.assertEqual(
            log,
            ["grandchild-cleanup", "child-cleanup", "parent-cleanup"],
        )


class TestEdgeCases(unittest.IsolatedAsyncioTestCase):
    async def test_double_cancel_is_safe(self):
        scope = CancelScope()
        scope.cancel()
        scope.cancel()
        self.assertTrue(scope.cancelled)

    async def test_cancel_before_enter(self):
        scope = CancelScope()
        scope.cancel()
        with self.assertRaises(ScopeCancelled):
            async with scope:
                pass

    async def test_child_created_after_parent_cancelled_is_cancelled(self):
        async with CancelScope() as parent:
            parent.cancel()
            child = parent.child()
            self.assertTrue(child.cancelled)
            with self.assertRaises(ScopeCancelled):
                async with child:
                    pass

    async def test_register_cleanup_after_exit_raises(self):
        scope = CancelScope()
        async with scope:
            pass
        with self.assertRaises(RuntimeError):
            scope.on_cleanup(lambda: None)

    async def test_child_after_parent_exited_raises(self):
        async with CancelScope() as parent:
            pass
        with self.assertRaises(RuntimeError):
            parent.child()

    async def test_reenter_raises(self):
        scope = CancelScope()
        async with scope:
            pass
        with self.assertRaises(RuntimeError):
            async with scope:
                pass

    async def test_cancel_after_exit_is_noop(self):
        async with CancelScope() as scope:
            pass
        scope.cancel()  # 不应抛错


if __name__ == "__main__":
    unittest.main()
