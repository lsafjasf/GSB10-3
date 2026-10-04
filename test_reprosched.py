#!/usr/bin/env python3
"""reprosched 自测：重放断言、遍历规模、四类场景、边界用例。

运行: python3 -m unittest test_reprosched -v
"""

import json
import unittest

import scenarios
from reprosched import (
    FirstReadyPolicy,
    RandomPolicy,
    ReplayError,
    ReplayPolicy,
    explore,
    run_program,
    to_json,
)


def run(factory, **kwargs):
    prog, check, _ = factory()
    return run_program(prog, check=check, **kwargs)


class TestExploration(unittest.TestCase):
    """遍历规模与发现缺陷数量（数据与 README 一致）。"""

    def test_no_race_has_no_bug(self):
        prog, check, _ = scenarios.no_race()
        stats = explore(prog, check=check)
        self.assertEqual(stats.schedules, 20)   # C(6,3)：两个线程各 3 个调度点
        self.assertEqual(stats.failing, 0)
        self.assertEqual(stats.defects, 0)

    def test_single_race_lost_update(self):
        prog, check, _ = scenarios.single_race()
        stats = explore(prog, check=check)
        self.assertEqual(stats.schedules, 20)
        self.assertEqual(stats.failing, 12)     # 12 条调度丢失更新
        self.assertEqual(stats.defects, 1)      # 归为同一个缺陷签名
        sig, (count, result) = next(iter(stats.signatures.items()))
        self.assertIn("lost update", sig)
        self.assertEqual(result.final_state["counter"], 1)

    def test_deadlock_detected(self):
        prog, check, _ = scenarios.deadlock()
        stats = explore(prog, check=check)
        self.assertEqual(stats.schedules, 68)
        self.assertEqual(stats.failing, 12)
        self.assertEqual(stats.defects, 1)
        sig, (count, result) = next(iter(stats.signatures.items()))
        self.assertEqual(result.outcome, "deadlock")

    def test_specific_interleaving(self):
        prog, check, _ = scenarios.specific_interleaving()
        stats = explore(prog, check=check)
        self.assertEqual(stats.schedules, 1680)
        self.assertEqual(stats.failing, 306)    # 只有特定交错才暴露
        self.assertEqual(stats.defects, 2)      # (flag=1,data=2) 与 (flag=2,data=1)
        self.assertLess(stats.failing, stats.schedules)

    def test_default_policy_misses_all_bugs(self):
        """默认确定性调度（总是选第一个可运行线程）一个缺陷也碰不到。"""
        for factory in (scenarios.single_race, scenarios.deadlock,
                        scenarios.specific_interleaving):
            result = run(factory, policy=FirstReadyPolicy())
            self.assertFalse(result.bug, factory.__name__)


class TestReplay(unittest.TestCase):
    """同一条记录的交错序列必须重放出完全相同的结果。"""

    def assert_replay_identical(self, recorded):
        prog, check, _ = getattr(scenarios, self.SCENARIO)()
        replayed = run_program(prog, policy=ReplayPolicy(recorded.trace), check=check)
        self.assertEqual(replayed.trace, recorded.trace)
        self.assertEqual(replayed.events, recorded.events)           # 可观察事件逐一相同
        self.assertEqual(replayed.final_state, recorded.final_state) # 最终状态相同
        self.assertEqual(replayed.outcome, recorded.outcome)
        self.assertEqual(replayed.bug, recorded.bug)
        return replayed

    SCENARIO = None

    def test_replay_all_scenarios(self):
        for name in ("no_race", "single_race", "deadlock", "specific_interleaving"):
            for seed in range(5):
                self.SCENARIO = name
                with self.subTest(scenario=name, seed=seed):
                    recorded = run(getattr(scenarios, name), policy=RandomPolicy(seed))
                    self.assert_replay_identical(recorded)

    def test_replay_reproduces_lost_update_exactly(self):
        """硬编码一条已知会丢失更新的交错序列，断言重放结果逐字节相同。"""
        trace = ["TA", "TA", "TB", "TB", "TA", "TB"]
        recorded = run(scenarios.single_race, policy=ReplayPolicy(trace))
        self.assertTrue(recorded.bug)
        self.assertEqual(recorded.final_state, {"counter": 1})
        self.assertIn("lost update", str(recorded.check_error))
        # 再重放一次，事件序列必须完全相同
        replayed = run(scenarios.single_race, policy=ReplayPolicy(trace))
        self.assertEqual(replayed.events, recorded.events)
        self.assertEqual(
            [e["event"] for e in replayed.events],
            ["start", "read counter -> 0", "start", "read counter -> 0",
             "write counter: 0 -> 1", "write counter: 1 -> 1"],
        )

    def test_replay_reproduces_deadlock(self):
        trace = ["TA", "TA", "TB", "TB", "TA", "TB"]  # TA 持 m1、TB 持 m2 后互相等待
        result = run(scenarios.deadlock, policy=ReplayPolicy(trace))
        self.assertEqual(result.outcome, "deadlock")
        self.assertTrue(result.bug)
        again = run(scenarios.deadlock, policy=ReplayPolicy(trace))
        self.assertEqual(again.events, result.events)
        self.assertEqual(again.outcome, "deadlock")

    def test_trace_survives_json_roundtrip(self):
        """交错序列序列化为 JSON 后仍能重放出相同结果。"""
        recorded = run(scenarios.specific_interleaving, policy=RandomPolicy(7))
        blob = json.loads(to_json(recorded, scenario="specific_interleaving"))
        replayed = run(scenarios.specific_interleaving,
                       policy=ReplayPolicy(blob["choices"]))
        self.assertEqual(replayed.events, recorded.events)
        self.assertEqual(replayed.final_state, recorded.final_state)


class TestEdgeCases(unittest.TestCase):
    def test_empty_program(self):
        result = run_program(lambda s: None)
        self.assertEqual(result.outcome, "ok")
        self.assertEqual(result.trace, [])
        stats = explore(lambda s: None)
        self.assertEqual((stats.schedules, stats.failing), (1, 0))

    def test_single_thread_single_schedule(self):
        def prog(s):
            x = s.var("x", 0)
            s.spawn(lambda: x.write(42), "T1")
        result = run_program(prog)
        self.assertEqual(result.final_state, {"x": 42})
        self.assertEqual(explore(prog).schedules, 1)

    def test_thread_without_points(self):
        """不含用户级调度点的线程：只剩启动点，2 个线程仅 2 条调度。"""
        def prog(s):
            s.spawn(lambda: sum(range(100)), "T1")
            s.spawn(lambda: sum(range(100)), "T2")
        self.assertEqual(explore(prog).schedules, 2)

    def test_self_deadlock(self):
        """同一线程重复获取同一把非可重入锁 => 死锁。"""
        def prog(s):
            m = s.mutex("m")
            def t():
                m.acquire()
                m.acquire()
            s.spawn(t, "T1")
        self.assertEqual(run_program(prog).outcome, "deadlock")

    def test_livelock_guard(self):
        """永不终止的程序被 max_steps 截断并判为 livelock。"""
        def prog(s):
            x = s.var("x", 0)
            def t():
                while True:
                    x.write(1)
            s.spawn(t, "T1")
        self.assertEqual(run_program(prog, max_steps=50).outcome, "livelock")

    def test_thread_exception_is_bug(self):
        def prog(s):
            def t():
                raise ValueError("boom")
            s.spawn(t, "T1")
        result = run_program(prog)
        self.assertTrue(result.bug)
        self.assertIsInstance(result.thread_errors["T1"], ValueError)

    def test_replay_trace_too_short(self):
        with self.assertRaises(ReplayError):
            run(scenarios.single_race, policy=ReplayPolicy(["TA"]))

    def test_replay_trace_wrong_thread(self):
        with self.assertRaises(ReplayError):
            run(scenarios.single_race, policy=ReplayPolicy(["TA", "TA", "TA"]))

    def test_replay_unknown_thread(self):
        with self.assertRaises(ReplayError):
            run(scenarios.single_race, policy=ReplayPolicy(["TX"]))

    def test_release_unlocked_mutex_raises(self):
        def prog(s):
            m = s.mutex("m")
            s.spawn(m.release, "T1")
        result = run_program(prog)
        self.assertTrue(result.bug)
        self.assertIsInstance(result.thread_errors["T1"], RuntimeError)

    def test_determinism_same_seed(self):
        a = run(scenarios.specific_interleaving, policy=RandomPolicy(99))
        b = run(scenarios.specific_interleaving, policy=RandomPolicy(99))
        self.assertEqual(a.events, b.events)
        self.assertEqual(a.trace, b.trace)


if __name__ == "__main__":
    unittest.main()
