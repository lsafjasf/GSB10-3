"""test_corpus.py — corpus.py 的自测（标准库 unittest）。

覆盖情形：
1. 全新覆盖增益 -> 入库
2. 完全重复输入 -> 丢弃
3. 有效但无增益 -> 丢弃
4. 容量打满 -> 触发冗余优先淘汰，覆盖不丢
5. 输入全部无效 -> 入库率为 0
6. 最小化 -> 入库体积显著小于原始体积
"""

import unittest

from corpus import Corpus, minimize, trace_coverage


def target(data):
    """测试目标：嵌套分支，深路径覆盖是浅路径的超集。"""
    if len(data) == 0:
        return False
    if data[0] != 0x41:            # 'A'
        return False
    if len(data) >= 2 and data[1] == 0x42:   # 'B'
        depth = 1
        if len(data) >= 3 and data[2] == 0x43:   # 'C'
            depth = 2
        return depth > 0
    return True


class TestCorpus(unittest.TestCase):
    def test_novel_gain_admitted(self):
        c = Corpus(target, capacity=10)
        self.assertTrue(c.add(b"A"))       # 覆盖主路径
        self.assertTrue(c.add(b"AB"))      # 新增 B 分支行
        self.assertTrue(c.add(b"ABC"))     # 新增 C 分支行
        self.assertEqual(c.n_admitted, 3)
        self.assertEqual(len(c.entries), 3)

    def test_exact_duplicate_discarded(self):
        c = Corpus(target, capacity=10)
        self.assertTrue(c.add(b"AB"))
        self.assertFalse(c.add(b"AB"))     # 完全重复
        self.assertEqual(c.n_dup, 1)
        self.assertEqual(c.n_admitted, 1)

    def test_valid_but_no_gain_discarded(self):
        c = Corpus(target, capacity=10)
        self.assertTrue(c.add(b"A"))
        self.assertFalse(c.add(b"A\x00"))  # 有效但走的行已被覆盖
        self.assertFalse(c.add(b"A\x01"))
        self.assertEqual(c.n_nogain, 2)
        self.assertEqual(c.n_admitted, 1)

    def test_capacity_full_eviction_keeps_coverage(self):
        c = Corpus(target, capacity=2)
        c.add(b"AB")      # 覆盖 {主路径, B 分支}
        c.add(b"ABC")     # 覆盖 {主路径, B 分支, C 分支} -> b"AB" 变为完全冗余
        cov_before = len(c.total_cov)
        c.add(b"A")       # 触发淘汰：容量 2 -> 3，须淘汰 1 条
        self.assertEqual(len(c.entries), 2)
        self.assertEqual(len(c.evictions), 1)
        before, after = c.evictions[0]
        self.assertEqual(before, after)            # 冗余淘汰，覆盖无损
        # b"A" 自身带来 1 行新增益，淘汰不丢行 -> 总覆盖 = 之前 + 1
        self.assertEqual(len(c.total_cov), cov_before + 1)

    def test_capacity_one_hard_bound(self):
        c = Corpus(target, capacity=1)
        self.assertTrue(c.add(b"A"))
        self.assertFalse(c.add(b"AB"))     # 满且无冗余 -> 拒收，覆盖不受损
        self.assertFalse(c.add(b"ABC"))
        self.assertLessEqual(len(c.entries), 1)    # 容量硬上界
        self.assertEqual(c.n_admitted, 1)
        self.assertEqual(c.n_full, 2)
        self.assertEqual(len(c.evictions), 0)
        self.assertEqual(c.entries[0][0], b"A")    # 原条目仍在

    def test_reject_when_full_keeps_coverage(self):
        def wide_target(data):
            if len(data) < 1:
                return False
            if data[0] == 0x01:
                return True
            if data[0] == 0x02:
                return True
            if data[0] == 0x03:
                return True
            return True

        c = Corpus(wide_target, capacity=2)
        c.add(b"\x01")
        c.add(b"\x02")
        cov = set(c.total_cov)
        self.assertFalse(c.add(b"\x03"))  # 有增益但满库且无冗余 -> 拒收
        self.assertEqual(c.n_full, 1)
        self.assertEqual(c.total_cov, cov)  # 覆盖只增不减
        self.assertEqual(len(c.entries), 2)

    def test_all_invalid_inputs(self):
        c = Corpus(target, capacity=10)
        for bad in (b"", b"X", b"ZZZ", b"\x00" * 16):
            self.assertFalse(c.add(bad))
        self.assertEqual(c.n_admitted, 0)
        self.assertEqual(c.n_invalid, 4)
        self.assertEqual(c.admission_rate, 0.0)
        self.assertEqual(len(c.entries), 0)

    def test_minimization_shrinks_input(self):
        c = Corpus(target, capacity=10)
        padded = b"AB" + b"\x00" * 200   # 后缀垃圾不影响覆盖
        self.assertTrue(c.add(padded))
        kept = c.entries[0][0]
        self.assertLess(len(kept), len(padded))
        self.assertLessEqual(len(kept), 3)         # 贪心块删除收敛到很短
        self.assertEqual(c.bytes_after_min, len(kept))
        self.assertEqual(c.bytes_before_min, len(padded))

    def test_minimize_preserves_interestingness(self):
        ok, cov = trace_coverage(target, b"ABC")

        def interesting(cand):
            ok2, c2 = trace_coverage(target, cand)
            return ok2 and cov <= c2

        shrunk = minimize(b"ABC" + b"\xff" * 50, interesting)
        ok3, cov3 = trace_coverage(target, shrunk)
        self.assertTrue(ok3)
        self.assertTrue(cov <= cov3)               # 覆盖一行不丢
        self.assertLess(len(shrunk), 10)

    def test_report_contains_key_metrics(self):
        c = Corpus(target, capacity=2)
        c.add(b"AB")
        c.add(b"ABC")
        c.add(b"A")
        text = c.report()
        for key in ("入库率", "最小化", "淘汰事件", "总覆盖行数"):
            self.assertIn(key, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
