"""cancelscope 自测：确定性时钟，覆盖正常/超时/失败/收尾/边界。"""
import unittest

from cancelscope import (
    COLLECT, FAIL_FAST, CancelToken, Cancelled, ChildErrors, ChildFailed,
    ManualClock, Scheduler, ScopeTimeout, Sleep,
)


class TestNormalCompletion(unittest.TestCase):
    def test_normal_completion(self):
        clock = ManualClock()
        sched = Scheduler(clock)
        done = []

        def root(scope):
            def make(i):
                def child(s):
                    yield Sleep(i)
                    done.append(i)
                    return i * 10
                return child
            handles = [scope.spawn(make(i), name=f"c{i}") for i in (1, 2, 3)]
            yield Sleep(3)
            return sorted(h.result for h in handles)

        result = sched.run(root, timeout=10)
        self.assertEqual(result, [10, 20, 30])
        self.assertEqual(done, [1, 2, 3])
        self.assertEqual(clock.now(), 3)          # 并行执行，总时长 = 最长子任务
        self.assertEqual(sched.active_count, 0)   # 资源全部释放

    def test_sync_root_function(self):
        sched = Scheduler(ManualClock())
        self.assertEqual(sched.run(lambda scope: 42), 42)
        self.assertEqual(sched.active_count, 0)


class TestParentTimeoutCascade(unittest.TestCase):
    def test_parent_timeout_cascades_to_all_descendants(self):
        clock = ManualClock()
        sched = Scheduler(clock)
        events = []
        active_seen = []

        def root(scope):
            def make(i):
                def child(s):
                    def grand(g):
                        active_seen.append(sched.active_count)
                        try:
                            yield Sleep(1000)
                        except Cancelled:
                            events.append(("grand-cancelled", clock.now()))
                            raise
                    if i == 0:
                        s.spawn(grand, name="grand")
                    active_seen.append(sched.active_count)
                    try:
                        yield Sleep(1000)
                    except Cancelled:
                        events.append(("cancelled", i, clock.now()))
                        raise
                return child
            for i in range(3):
                scope.spawn(make(i), name=f"child-{i}")
            yield Sleep(1000)

        with self.assertRaises(ScopeTimeout):
            sched.run(root, timeout=5)

        # 取消断言：未取消子任务数为零（含孙任务）
        self.assertEqual(sched.uncancelled_count, 0)
        # 3 个子任务 + 1 个孙任务都在 t=5 收到取消信号
        cancelled = [e for e in events if e[0] == "cancelled"]
        self.assertEqual(sorted(cancelled),
                         sorted([("cancelled", i, 5) for i in range(3)]))
        self.assertEqual(events.count(("grand-cancelled", 5)), 1)
        # 资源释放：活动任务数 5 -> 0
        peak = max(active_seen)
        self.assertEqual(peak, 5)  # root + 3 children + 1 grandchild
        self.assertEqual(sched.active_count, 0)
        print(f"\n[父超时] 取消前活动任务数={peak} -> 取消后={sched.active_count}, "
              f"未取消子任务数={sched.uncancelled_count}, 虚拟时间={clock.now()}s")


class TestChildFailurePolicy(unittest.TestCase):
    def test_fail_fast_cancels_siblings_and_ends_parent_early(self):
        clock = ManualClock()
        sched = Scheduler(clock)
        events = []

        def root(scope):
            def boom(s):
                yield Sleep(2)
                raise ValueError("boom")

            def make_slow(i):
                def child(s):
                    try:
                        yield Sleep(100)
                    except Cancelled:
                        events.append(("cancelled", i, clock.now()))
                        raise
                return child
            scope.spawn(boom, name="boom")
            scope.spawn(make_slow(1), name="slow-1")
            scope.spawn(make_slow(2), name="slow-2")
            yield Sleep(100)

        with self.assertRaises(ChildFailed) as cm:
            sched.run(root, timeout=1000, policy=FAIL_FAST)
        self.assertEqual(cm.exception.task_name, "boom")
        self.assertIsInstance(cm.exception.error, ValueError)
        # 兄弟任务在失败发生的同一时刻 t=2 被取消，父任务提前结束（远小于 100）
        self.assertEqual(sorted(events),
                         sorted([("cancelled", 1, 2), ("cancelled", 2, 2)]))
        self.assertEqual(clock.now(), 2)
        self.assertEqual(sched.uncancelled_count, 0)
        self.assertEqual(sched.active_count, 0)
        print(f"\n[子失败/FAIL_FAST] t=2 子任务失败, 兄弟立即取消, "
              f"父任务 t={clock.now()}s 提前结束 (原计划 100s)")

    def test_collect_waits_for_all_and_aggregates(self):
        clock = ManualClock()
        sched = Scheduler(clock)
        finished = []

        def root(scope):
            def make_failer(i, t):
                def child(s):
                    yield Sleep(t)
                    raise RuntimeError(f"err-{i}")
                return child

            def ok(s):
                yield Sleep(5)
                finished.append("ok")
            scope.spawn(make_failer(1, 1), name="f1")
            scope.spawn(make_failer(2, 2), name="f2")
            scope.spawn(ok, name="ok")
            yield Sleep(5)

        with self.assertRaises(ChildErrors) as cm:
            sched.run(root, timeout=100, policy=COLLECT)
        self.assertEqual(len(cm.exception.errors), 2)
        self.assertEqual([n for n, _ in cm.exception.errors], ["f1", "f2"])
        self.assertEqual(finished, ["ok"])   # 兄弟不被取消，正常跑完
        self.assertEqual(clock.now(), 5)     # 等待全部结束后才返回
        print(f"\n[子失败/COLLECT] 2 个失败被汇总, 兄弟任务跑完, "
              f"父任务 t={clock.now()}s 结束")


class TestCleanupAfterCancel(unittest.TestCase):
    def test_cleanup_runs_to_completion_after_cancel(self):
        clock = ManualClock()
        sched = Scheduler(clock)
        log = []

        def root(scope):
            def child(s):
                try:
                    yield Sleep(100)
                except Cancelled:
                    log.append(("cleanup-start", clock.now(), sched.active_count))
                    yield Sleep(2)  # 收尾：释放连接/文件句柄
                    log.append(("cleanup-done", clock.now()))
                    raise
            scope.spawn(child, name="worker")
            yield Sleep(100)

        with self.assertRaises(ScopeTimeout):
            sched.run(root, timeout=3)

        # 取消发生在 t=3，收尾被允许执行到 t=5 才结束
        self.assertEqual(log[0], ("cleanup-start", 3, 2))
        self.assertEqual(log[1], ("cleanup-done", 5))
        self.assertEqual(clock.now(), 5)
        # 收尾完成后资源才释放
        self.assertEqual(sched.active_count, 0)
        self.assertEqual(sched.uncancelled_count, 0)
        print(f"\n[取消后收尾] 取消@t=3s, 收尾耗时 2s, 结束@t={clock.now()}s, "
              f"活动任务 {log[0][2]} -> {sched.active_count}")


class TestEdgeCases(unittest.TestCase):
    def test_zero_timeout_cancels_before_first_step(self):
        started = []

        def root(scope):
            started.append(True)
            yield Sleep(1)
        sched = Scheduler(ManualClock())
        with self.assertRaises(ScopeTimeout):
            sched.run(root, timeout=0)
        self.assertEqual(started, [])  # 根任务尚未开始即被取消

    def test_nested_timeout_propagates_when_uncaught(self):
        def root(scope):
            def child(s):
                yield Sleep(100)
            scope.spawn(child, name="inner", timeout=2)
            yield Sleep(100)
        sched = Scheduler(ManualClock())
        with self.assertRaises(ChildFailed) as cm:
            sched.run(root, timeout=50)
        self.assertIsInstance(cm.exception.error, ScopeTimeout)
        self.assertEqual(sched.uncancelled_count, 0)

    def test_nested_timeout_can_be_caught_for_fallback(self):
        def root(scope):
            def child(s):
                try:
                    yield Sleep(100)
                except Cancelled:
                    return "fallback"  # 内层吞掉超时，返回降级结果
            handle = scope.spawn(child, name="inner", timeout=2)
            yield Sleep(10)
            return handle.result
        sched = Scheduler(ManualClock())
        self.assertEqual(sched.run(root, timeout=50), "fallback")

    def test_timeout_while_waiting_for_children(self):
        # 父任务体已结束，但子任务未结束且父 deadline 到期 => 仍算超时
        def root(scope):
            def child(s):
                yield Sleep(100)
            scope.spawn(child, name="slow-child")
            # 根任务体立即返回，但作用域要等子任务
        sched = Scheduler(ManualClock())
        with self.assertRaises(ScopeTimeout):
            sched.run(root, timeout=5)
        self.assertEqual(sched.uncancelled_count, 0)

    def test_cancel_is_idempotent_and_keeps_first_reason(self):
        token = CancelToken()
        child = CancelToken(token)
        token.cancel("timeout")
        token.cancel("parent")
        self.assertEqual(token.reason, "timeout")
        self.assertTrue(child.cancelled)
        self.assertEqual(child.reason, "timeout")

    def test_spawn_into_cancelled_scope_is_rejected(self):
        got = []

        def root(scope):
            def failer(s):
                yield Sleep(1)
                raise ValueError("x")

            def late_spawner(s):
                try:
                    yield Sleep(2)  # t=1 时 failer 已失败并取消整个作用域
                except Cancelled:
                    try:
                        s.spawn(failer, name="late")
                    except Cancelled:
                        got.append("blocked")
                    raise
            scope.spawn(failer, name="failer")
            scope.spawn(late_spawner, name="late-spawner")
            yield Sleep(100)
        sched = Scheduler(ManualClock())
        with self.assertRaises(ChildFailed):
            sched.run(root, timeout=1000)
        self.assertEqual(got, ["blocked"])

    def test_negative_sleep_rejected(self):
        with self.assertRaises(ValueError):
            Sleep(-1)

    def test_unknown_command_fails_task(self):
        def root(scope):
            yield "not-a-sleep"
        sched = Scheduler(ManualClock())
        with self.assertRaises(TypeError):
            sched.run(root)

    def test_fail_fast_reports_first_failure(self):
        # 同一时刻多个失败：FAIL_FAST 下第一个失败取消其余兄弟，
        # 父任务收到第一个错误
        def root(scope):
            def make(i):
                def child(s):
                    yield Sleep(1)
                    raise RuntimeError(f"e{i}")
                return child
            scope.spawn(make(1), name="a")
            scope.spawn(make(2), name="b")
            yield Sleep(10)
        sched = Scheduler(ManualClock())
        with self.assertRaises(ChildFailed) as cm:
            sched.run(root, timeout=100)
        self.assertEqual(cm.exception.task_name, "b")  # LIFO: 后注册先执行
        self.assertEqual(sched.uncancelled_count, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
