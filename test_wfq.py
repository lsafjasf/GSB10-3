"""WeightedFairScheduler 自测：公平性、无饥饿、确定性、边界用例。

运行：python3 test_wfq.py [-v]
"""

import unittest

from wfq import WeightedFairScheduler


def run_sequence(sched, n):
    """连续取 n 个请求，返回 (类别名, 请求) 序列。"""
    return [sched.next_request() for _ in range(n)]


def make_scheduler(weights, backlog=0):
    sched = WeightedFairScheduler()
    for name, w in weights.items():
        sched.add_class(name, w)
        for i in range(backlog):
            sched.submit(name, i)
    return sched


class TestSingleClass(unittest.TestCase):
    """单类别：所有机会都给这一类，顺序 FIFO。"""

    def test_all_requests_served_in_fifo_order(self):
        sched = make_scheduler({"only": 7}, backlog=5)
        seq = run_sequence(sched, 5)
        self.assertEqual(seq, [("only", i) for i in range(5)])

    def test_empty_scheduler_returns_none(self):
        sched = make_scheduler({"only": 1})
        self.assertIsNone(sched.next_request())
        self.assertIsNone(sched.next_request())


class TestWeightProportionality(unittest.TestCase):
    """多类别权重悬殊：处理次数与权重成比例。"""

    def assert_proportional(self, weights, n, tol):
        sched = make_scheduler(weights, backlog=n)
        counts = {name: 0 for name in weights}
        for name, _ in run_sequence(sched, n):
            counts[name] += 1
        total_weight = sum(weights.values())
        for name, w in weights.items():
            expected = n * w / total_weight
            self.assertAlmostEqual(counts[name], expected, delta=tol,
                                   msg=f"{name}: 期望 {expected}，实际 {counts[name]}")
        return counts

    def test_moderate_weights_5_3_2(self):
        counts = self.assert_proportional({"a": 5, "b": 3, "c": 2}, 10000, tol=2)
        # SWRR 在整周期上应当精确命中比例
        self.assertEqual(counts, {"a": 5000, "b": 3000, "c": 2000})

    def test_extreme_disparity_1000_1(self):
        counts = self.assert_proportional({"high": 1000, "low": 1}, 100100, tol=2)
        self.assertEqual(counts["low"], 100)  # 低权重仍稳定获得 1/1001 的机会

    def test_extreme_disparity_three_classes(self):
        self.assert_proportional({"a": 100, "b": 10, "c": 1}, 11100, tol=2)


class TestNoStarvationUnderPressure(unittest.TestCase):
    """持续高压：高优先级流量源源不断，低优先级仍被处理。"""

    def test_low_priority_served_under_sustained_high_pressure(self):
        sched = WeightedFairScheduler()
        sched.add_class("high", 1000)
        sched.add_class("low", 1)

        ticks = 100000
        counts = {"high": 0, "low": 0}
        waits = []
        arrived = {}
        low_seq = 0
        for tick in range(ticks):
            sched.submit("high", tick)  # 高优先级持续不断
            if tick % 2000 == 0:  # 到达速率低于 low 的服务份额 1/1001
                sched.submit("low", low_seq)
                arrived[low_seq] = tick
                low_seq += 1
            name, item = sched.next_request()
            counts[name] += 1
            if name == "low":
                waits.append(tick - arrived.pop(item))

        # 数据：高压下所有到达的 low 请求都被处理，无一遗漏
        self.assertEqual(counts["low"], low_seq)
        self.assertEqual(sched.pending("low"), 0)
        # 无饥饿的核心断言：每个 low 请求的等待都不超过理论上界 1001
        self.assertTrue(waits, "高压期间 low 请求一个都没被处理（饥饿！）")
        self.assertLessEqual(max(waits), 1001)
        print(f"\n[公平性数据] 高压 1000:1 持续 {ticks} 次调度: "
              f"high={counts['high']}, low={counts['low']}, "
              f"low 最大等待={max(waits)} (上界 1001)")

    def test_max_gap_between_low_services_bounded(self):
        # 低优先级两次被处理之间的间隔不超过 total_weight / weight_low
        sched = make_scheduler({"high": 50, "low": 1}, backlog=10000)
        last_low_at = -1
        max_gap = 0
        for tick in range(5100):
            name, _ = sched.next_request()
            if name == "low":
                if last_low_at >= 0:
                    max_gap = max(max_gap, tick - last_low_at)
                last_low_at = tick
        self.assertLessEqual(max_gap, 51)


class TestZeroWeight(unittest.TestCase):
    """权重为零的明确定义语义。"""

    def test_zero_weight_starved_only_while_positive_has_backlog(self):
        sched = WeightedFairScheduler()
        sched.add_class("vip", 1)
        sched.add_class("free", 0)
        sched.submit("vip", "v1")
        sched.submit("free", "f1")
        # 正权重有积压时，0 权重类别不被选中
        self.assertEqual(sched.next_request(), ("vip", "v1"))
        # 正权重清空后，0 权重类别才被服务（不死锁）
        self.assertEqual(sched.next_request(), ("free", "f1"))
        self.assertIsNone(sched.next_request())

    def test_all_zero_weight_served_in_registration_fifo(self):
        sched = WeightedFairScheduler()
        sched.add_class("a", 0)
        sched.add_class("b", 0)
        sched.submit("a", 1)
        sched.submit("b", 2)
        sched.submit("a", 3)
        # 全部 0 权重：按注册顺序逐类 FIFO 轮询
        seq = run_sequence(sched, 3)
        self.assertEqual(seq, [("a", 1), ("b", 2), ("a", 3)])

    def test_zero_weight_never_beats_positive_weight(self):
        sched = make_scheduler({"p": 1, "z": 0}, backlog=100)
        for _ in range(100):
            sched.submit("z", "x")
        seq = run_sequence(sched, 100)
        self.assertTrue(all(name == "p" for name, _ in seq))


class TestDeterminism(unittest.TestCase):
    """确定性：相同输入序列 -> 相同输出序列。"""

    def build_and_run(self):
        sched = WeightedFairScheduler()
        for name, w in [("gold", 8), ("silver", 3), ("bronze", 1), ("guest", 0)]:
            sched.add_class(name, w)
        out = []
        for tick in range(2000):
            # 确定性的"伪随机"到达模式（不依赖随机数生成器状态）
            if tick % 2 == 0:
                sched.submit("gold", ("g", tick))
            if tick % 3 == 0:
                sched.submit("silver", ("s", tick))
            if tick % 7 == 0:
                sched.submit("bronze", ("b", tick))
            if tick % 11 == 0:
                sched.submit("guest", ("u", tick))
            out.append(sched.next_request())
        return out

    def test_same_input_same_output(self):
        first = self.build_and_run()
        second = self.build_and_run()
        self.assertEqual(first, second)

    def test_tie_break_by_registration_order(self):
        # 权重相同 -> 严格按注册顺序轮转，结果可精确预测
        sched = make_scheduler({"x": 1, "y": 1, "z": 1}, backlog=3)
        names = [name for name, _ in run_sequence(sched, 9)]
        self.assertEqual(names, ["x", "y", "z"] * 3)


class TestEdgeCases(unittest.TestCase):
    """边界与非法输入。"""

    def test_negative_weight_rejected(self):
        sched = WeightedFairScheduler()
        with self.assertRaises(ValueError):
            sched.add_class("bad", -1)

    def test_non_int_weight_rejected(self):
        sched = WeightedFairScheduler()
        with self.assertRaises(TypeError):
            sched.add_class("bad", 1.5)
        with self.assertRaises(TypeError):
            sched.add_class("bad2", True)

    def test_duplicate_class_rejected(self):
        sched = WeightedFairScheduler()
        sched.add_class("a", 1)
        with self.assertRaises(ValueError):
            sched.add_class("a", 2)

    def test_submit_to_unknown_class(self):
        sched = WeightedFairScheduler()
        with self.assertRaises(KeyError):
            sched.submit("ghost", 1)

    def test_dynamic_arrival_and_drain(self):
        # 类别可以暂时无积压，之后再来请求仍被公平调度
        sched = WeightedFairScheduler()
        sched.add_class("a", 3)
        sched.add_class("b", 1)
        sched.submit("a", 1)
        self.assertEqual(sched.next_request(), ("a", 1))
        self.assertIsNone(sched.next_request())  # 全部排空
        sched.submit("b", 2)
        sched.submit("a", 3)
        self.assertEqual(sched.next_request(), ("a", 3))
        self.assertEqual(sched.next_request(), ("b", 2))

    def test_pending_counts(self):
        sched = make_scheduler({"a": 1, "b": 2}, backlog=3)
        self.assertEqual(sched.pending(), 6)
        self.assertEqual(sched.pending("a"), 3)
        sched.next_request()
        self.assertEqual(sched.pending(), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
