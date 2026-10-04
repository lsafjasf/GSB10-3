"""HierarchicalTimingWheel 自测（仅标准库 unittest，逻辑时间完全注入）。"""

import random
import unittest

from timing_wheel import HierarchicalTimingWheel


def layer_of(wheel, node):
    """白盒辅助：返回节点当前所在层号；已被回收/触发则返回 None。"""
    for level, layer in enumerate(wheel._layers):
        for bucket in layer.buckets:
            if node in bucket:
                return level
    return None


def assert_firing_order(testcase, fired):
    """触发顺序不变式：deadline 非降；相同 deadline 严格按插入序号升序。"""
    deadlines = [n.deadline for n in fired]
    testcase.assertEqual(deadlines, sorted(deadlines))
    by_deadline = {}
    for n in fired:
        by_deadline.setdefault(n.deadline, []).append(n.seq)
    for seqs in by_deadline.values():
        testcase.assertEqual(seqs, sorted(seqs))


class ImmediateTimeoutTest(unittest.TestCase):
    def test_zero_delay_fires_on_next_advance(self):
        w = HierarchicalTimingWheel(base_interval=1, slots_per_layer=8, start_time=0)
        a = w.insert(0, "now")
        b = w.insert(5, "later")
        fired = w.advance(0)
        self.assertEqual([n.payload for n in fired], ["now"])
        self.assertTrue(a.fired)
        self.assertFalse(b.fired)
        self.assertEqual([n.payload for n in w.advance(5)], ["later"])

    def test_immediate_among_others_keeps_insertion_order(self):
        w = HierarchicalTimingWheel(1, 8, 0)
        w.insert(10, "bucket-first")   # deadline 10，先进桶
        w.advance(10)                  # 推进到 10，触发上面这个
        late = w.insert(0, "due-now")  # 立即超时
        fired = w.advance(10)
        self.assertEqual([n.payload for n in fired], ["due-now"])
        self.assertTrue(late.fired)


class CrossLayerCascadeTest(unittest.TestCase):
    def test_step_by_step_cascade_and_exact_fire_time(self):
        # 每层 4 槽：span 依次为 4 / 16 / 64 / 256，delay=200 会溢出到第 3 层
        w = HierarchicalTimingWheel(base_interval=1, slots_per_layer=4, start_time=0)
        node = w.insert(200, "deep")
        self.assertEqual(w.levels, 4)
        self.assertEqual(layer_of(w, node), 3)

        fired = []
        for t in range(1, 201):
            fired.extend(w.advance(t))
            if t == 191:
                self.assertEqual(layer_of(w, node), 3)   # 尚未到达高层桶边界
            if t == 192:
                self.assertLess(layer_of(w, node), 3)    # 跨过边界后已降级
            if t < 200:
                self.assertEqual(fired, [], "不得在 deadline 之前触发")

        self.assertEqual([n.payload for n in fired], ["deep"])
        self.assertTrue(node.fired)
        self.assertEqual(len(w), 0)

    def test_huge_delay_grows_layers_and_fires_exactly(self):
        w = HierarchicalTimingWheel(base_interval=1, slots_per_layer=8, start_time=0)
        node = w.insert(10 ** 9, "far")
        self.assertGreater(w.levels, 3)
        # 空层跳转优化：一次跨 1e9 个 tick 的 advance 不会逐 tick 空转
        self.assertEqual(w.advance(10 ** 9 - 1), [])
        self.assertFalse(node.fired)
        fired = w.advance(10 ** 9)
        self.assertEqual([n.payload for n in fired], ["far"])

    def test_many_timers_scattered_across_layers(self):
        rng = random.Random(7)
        w = HierarchicalTimingWheel(1, 8, 0)
        delays = sorted(rng.randrange(0, 200_000) for _ in range(3000))
        nodes = [w.insert(d, d) for d in delays]
        fired = w.advance(200_000)
        self.assertEqual([n.deadline for n in fired], delays)
        self.assertTrue(all(n.fired for n in nodes))
        self.assertEqual(len(w), 0)


class CancellationTest(unittest.TestCase):
    def test_cancel_is_lazy_node_stays_in_bucket_until_advance(self):
        w = HierarchicalTimingWheel(1, 8, 0)
        node = w.insert(100, "x")
        self.assertIsNotNone(layer_of(w, node))
        self.assertTrue(node.cancel())
        # 懒惰：取消后节点仍物理留在桶里
        self.assertIsNotNone(layer_of(w, node))
        self.assertEqual(len(w), 0)  # 但待处理计数立即减少
        self.assertEqual(w.advance(100), [])
        self.assertFalse(node.fired)
        # 推进到该桶时被回收
        self.assertIsNone(layer_of(w, node))

    def test_cancel_semantics(self):
        w = HierarchicalTimingWheel(1, 8, 0)
        a = w.insert(10, "a")
        b = w.insert(10, "b")
        self.assertTrue(a.cancel())
        self.assertFalse(a.cancel(), "重复取消返回 False")
        fired = w.advance(10)
        self.assertEqual([n.payload for n in fired], ["b"])
        self.assertFalse(a.cancel(), "已取消不可再操作")
        self.assertFalse(b.cancel(), "已触发不可取消")

    def test_mass_cancellation_trigger_count_zero(self):
        rng = random.Random(20261004)
        w = HierarchicalTimingWheel(1, 16, 0)
        total = 5000
        nodes = [w.insert(rng.randrange(0, 100_000), i) for i in range(total)]
        cancelled = set()
        for n in nodes:
            if rng.random() < 0.9:
                self.assertTrue(n.cancel())
                cancelled.add(n.seq)
        self.assertEqual(len(w), total - len(cancelled))

        fired = w.advance(100_000)

        # 触发计数：每个被取消任务的触发次数必须为零
        trigger_count = {n.seq: 0 for n in nodes}
        for n in fired:
            trigger_count[n.seq] += 1
        for seq in cancelled:
            self.assertEqual(trigger_count[seq], 0)
            self.assertFalse(nodes[seq].fired)

        # 未取消的任务全部触发且恰好一次
        self.assertEqual(sorted(n.seq for n in fired),
                         sorted(set(range(total)) - cancelled))
        # 触发顺序不变式
        assert_firing_order(self, fired)
        self.assertEqual(len(w), 0)


class SameTickOrderTest(unittest.TestCase):
    def test_same_tick_batch_fires_in_insertion_order(self):
        w = HierarchicalTimingWheel(1, 8, 0)
        expected = []
        for i in range(2000):
            if i % 3 == 0:
                w.insert(7, ("other", i))
            else:
                w.insert(50, ("batch", i))
                expected.append(i)
        fired = w.advance(50)
        batch = [n.payload[1] for n in fired if n.payload[0] == "batch"]
        others = [n.payload[1] for n in fired if n.payload[0] == "other"]
        self.assertEqual(batch, expected, "同一时刻必须按插入顺序触发")
        self.assertEqual(others, list(range(0, 2000, 3)))
        assert_firing_order(self, fired)

    def test_same_deadline_inserted_at_different_times(self):
        w = HierarchicalTimingWheel(1, 8, 0)
        first = w.insert(100, "first")   # t=0 插入，先进高层桶
        w.advance(50)                    # 推进一半
        second = w.insert(50, "second")  # deadline 同样是 100
        third = w.insert(50, "third")
        fired = w.advance(100)
        self.assertEqual([n.payload for n in fired],
                         ["first", "second", "third"])
        self.assertLess(first.seq, second.seq)
        self.assertLess(second.seq, third.seq)


class EdgeCaseTest(unittest.TestCase):
    def test_backward_time_rejected(self):
        w = HierarchicalTimingWheel(1, 8, 100)
        w.advance(200)
        with self.assertRaises(ValueError):
            w.advance(199)

    def test_negative_delay_rejected(self):
        w = HierarchicalTimingWheel(1, 8, 0)
        with self.assertRaises(ValueError):
            w.insert(-1)

    def test_unaligned_start_time(self):
        w = HierarchicalTimingWheel(base_interval=10, slots_per_layer=8, start_time=13)
        a = w.insert(0, "a")   # deadline 13，落在当前 tick [10,20) → 立即到期
        b = w.insert(5, "b")   # deadline 18，同一 tick → 同样立即到期
        c = w.insert(25, "c")  # deadline 38 → 落入 bucket [30,40)
        fired = w.advance(13)
        self.assertEqual([n.payload for n in fired], ["a", "b"])
        self.assertEqual(w.advance(29), [])
        self.assertEqual([n.payload for n in w.advance(30)], ["c"])
        self.assertTrue(c.fired)

    def test_base_interval_quantization(self):
        # base_interval=10：deadline 量化到所属 tick，在 tick 起点触发
        w = HierarchicalTimingWheel(base_interval=10, slots_per_layer=8, start_time=0)
        node = w.insert(25, "q")  # deadline 25 ∈ tick [20,30)
        self.assertEqual(w.advance(19), [])
        fired = w.advance(20)
        self.assertEqual([n.payload for n in fired], ["q"])
        self.assertEqual(node.deadline, 25)

    def test_incremental_and_single_shot_advance_agree(self):
        rng = random.Random(99)
        delays = [rng.randrange(0, 50_000) for _ in range(800)]

        w1 = HierarchicalTimingWheel(1, 8, 0)
        for d in delays:
            w1.insert(d, d)
        fired1 = w1.advance(50_000)

        w2 = HierarchicalTimingWheel(1, 8, 0)
        for d in delays:
            w2.insert(d, d)
        fired2 = []
        for t in range(0, 50_001, 137):  # 不规则步长逐步推进
            fired2.extend(w2.advance(min(t, 50_000)))
        fired2.extend(w2.advance(50_000))

        self.assertEqual([n.seq for n in fired1], [n.seq for n in fired2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
