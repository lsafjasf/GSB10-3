"""
range_scan 自测（仅标准库 unittest）。

运行：python3 -m unittest -v test_rangescan
     或  python3 test_rangescan.py

边界语义：左闭右开 [lo, hi)。
"""

import random
import unittest
import json
import os

from rangescan import BPlusTree, PageStore, ScanResult

GOLDEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "golden_data.json")


def build_tree(keys, max_keys=8):
    """按给定主键建一棵树，value = key * 10。"""
    store = PageStore()
    tree = BPlusTree(store, max_keys=max_keys)
    for k in keys:
        tree.insert(k, k * 10)
    return tree, store


def reference_filter(pairs, lo, hi):
    """对拍参考实现：全量数据排序后按 [lo, hi) 过滤。"""
    return [(k, v) for k, v in sorted(pairs) if lo <= k < hi]


def leaf_id_of(tree, key):
    return tree._locate(key)[0]


# ---------------------------------------------------------------------------
# 1. 边界语义：左闭右开 [lo, hi)
# ---------------------------------------------------------------------------
class BoundarySemanticsTest(unittest.TestCase):
    def setUp(self):
        self.tree, _ = build_tree(range(0, 100), max_keys=8)

    def test_left_closed(self):
        # 左边界 lo 本身存在 -> 必须包含
        res = self.tree.range_scan(10, 20)
        self.assertEqual(res.items[0], (10, 100))
        # lo 闭：包含 10
        self.assertEqual(res.items[-1], (19, 190))         # hi 开：不含 20
        self.assertEqual([k for k, _ in res.items], list(range(10, 20)))
        self.assertEqual(res.skipped, [])

    def test_right_open(self):
        res = self.tree.range_scan(10, 11)
        self.assertEqual(res.items, [(10, 100)])           # 只含 lo，不含 hi

    def test_boundary_keys_exact_hit(self):
        # lo/hi 恰好命中已有主键
        res = self.tree.range_scan(0, 99)
        self.assertEqual(res.items[0], (0, 0))
        self.assertEqual(res.items[-1], (98, 980))         # 99 被右开排除

    def test_out_of_domain_bounds(self):
        res = self.tree.range_scan(-1000, 1000)
        self.assertEqual(len(res.items), 100)

    def test_empty_interval_equal(self):
        # lo == hi -> 空区间，不读任何数据页
        self.tree.store.read_log.clear()
        res = self.tree.range_scan(50, 50)
        self.assertEqual(res, ScanResult([], []))
        self.assertEqual(self.tree.store.read_log, [])

    def test_empty_interval_reversed(self):
        # lo > hi -> 空区间
        res = self.tree.range_scan(60, 40)
        self.assertEqual(res, ScanResult([], []))


# ---------------------------------------------------------------------------
# 2. 空区间 / 空树 / 落在键隙之间
# ---------------------------------------------------------------------------
class EmptyIntervalTest(unittest.TestCase):
    def test_empty_tree(self):
        tree, _ = build_tree([], max_keys=4)
        self.assertEqual(tree.range_scan(0, 100), ScanResult([], []))

    def test_gap_between_keys(self):
        tree, _ = build_tree([10, 20, 30], max_keys=4)
        self.assertEqual(tree.range_scan(11, 20).items, [])  # (11,20) 内无键
        self.assertEqual(tree.range_scan(0, 10).items, [])   # hi=10 右开排除

    def test_no_keys_in_range_but_pages_read(self):
        tree, _ = build_tree(range(0, 50), max_keys=4)
        res = tree.range_scan(200, 300)                      # 区间在最大键之外
        self.assertEqual(res.items, [])
        self.assertEqual(res.skipped, [])


# ---------------------------------------------------------------------------
# 3. 单页内的范围
# ---------------------------------------------------------------------------
class SinglePageTest(unittest.TestCase):
    def test_range_within_one_leaf(self):
        tree, store = build_tree(range(0, 6), max_keys=16)   # 全部键在一个叶子页
        store.read_log.clear()
        res = tree.range_scan(2, 5)
        self.assertEqual(res.items, [(2, 20), (3, 30), (4, 40)])
        touched = {pid for _, pid in store.read_log}
        self.assertEqual(len(touched), 1)                    # 确实只读了一页


# ---------------------------------------------------------------------------
# 4. 跨多页
# ---------------------------------------------------------------------------
class MultiPageTest(unittest.TestCase):
    def test_cross_many_leaves(self):
        keys = list(range(0, 200))
        tree, store = build_tree(keys, max_keys=8)
        res = tree.range_scan(23, 177)
        self.assertEqual(res.items, reference_filter([(k, k * 10) for k in keys], 23, 177))
        ok_pages = {pid for tag, pid in store.read_log if tag == "ok"}
        self.assertGreater(len(ok_pages), 3)                 # 确认真的跨了多页

    def test_whole_chain(self):
        keys = list(range(0, 100))
        tree, _ = build_tree(keys, max_keys=4)
        res = tree.range_scan(0, 100)
        self.assertEqual(res.items, [(k, k * 10) for k in keys])


# ---------------------------------------------------------------------------
# 5. 页不可读：跳过并记录，扫描不中断
# ---------------------------------------------------------------------------
class UnreadablePageTest(unittest.TestCase):
    def setUp(self):
        self.keys = list(range(0, 100))
        self.tree, self.store = build_tree(self.keys, max_keys=8)

    def _page_keys(self, pid):
        page = self.store.get_for_write(pid)
        return list(page.keys)

    def test_skip_middle_page(self):
        pid = leaf_id_of(self.tree, 40)
        lost = self._page_keys(pid)
        self.store.bad_pages.add(pid)

        res = self.tree.range_scan(0, 100)
        expect = reference_filter(
            [(k, k * 10) for k in self.keys if k not in lost], 0, 100)
        self.assertEqual(res.items, expect)                  # 其余键一个不少
        self.assertEqual(res.skipped, [pid])                 # 坏页被记录
        self.assertIn((99, 990), res.items)                  # 扫描越过坏页直到链尾

    def test_skip_multiple_pages(self):
        pids = [leaf_id_of(self.tree, 20), leaf_id_of(self.tree, 60)]
        lost = set()
        for pid in pids:
            lost.update(self._page_keys(pid))
            self.store.bad_pages.add(pid)

        res = self.tree.range_scan(0, 100)
        expect = reference_filter(
            [(k, k * 10) for k in self.keys if k not in lost], 0, 100)
        self.assertEqual(res.items, expect)
        self.assertEqual(res.skipped, pids)                  # 按遇到顺序记录

    def test_skip_first_page_of_range(self):
        pid = leaf_id_of(self.tree, 0)
        lost = self._page_keys(pid)
        self.store.bad_pages.add(pid)

        res = self.tree.range_scan(0, 100)
        expect = reference_filter(
            [(k, k * 10) for k in self.keys if k not in lost], 0, 100)
        self.assertEqual(res.items, expect)
        self.assertEqual(res.skipped[0], pid)

    def test_skip_last_leaf_of_chain(self):
        pid = leaf_id_of(self.tree, 99)
        lost = self._page_keys(pid)
        self.store.bad_pages.add(pid)

        res = self.tree.range_scan(0, 100)
        expect = reference_filter(
            [(k, k * 10) for k in self.keys if k not in lost], 0, 100)
        self.assertEqual(res.items, expect)                  # 最后一页读不出也能正常收尾
        self.assertEqual(res.skipped, [pid])

    def test_heal_and_rescan(self):
        pid = leaf_id_of(self.tree, 40)
        self.store.bad_pages.add(pid)
        partial = self.tree.range_scan(0, 100)
        self.assertEqual(partial.skipped, [pid])

        self.store.bad_pages.discard(pid)                    # 页恢复可读
        full = self.tree.range_scan(0, 100)
        self.assertEqual(full.items, [(k, k * 10) for k in self.keys])
        self.assertEqual(full.skipped, [])


# ---------------------------------------------------------------------------
# 6. 扫描途中页被分裂
# ---------------------------------------------------------------------------
class SplitDuringScanTest(unittest.TestCase):
    def test_split_ahead_of_cursor(self):
        # 扫描读到中途时，前方页因插入而分裂；结果须等于分裂后全量过滤
        tree, store = build_tree(range(0, 50), max_keys=8)
        pages_before = store.page_count
        fired = {"done": False}

        def hook(pid):
            if not fired["done"] and pid == leaf_id_of(tree, 16):
                fired["done"] = True
                for k in range(60, 77):      # 落在游标前方的页里，触发分裂
                    tree.insert(k, k * 10)

        store.on_read = hook
        res = tree.range_scan(0, 100)
        store.on_read = None

        self.assertTrue(fired["done"])
        self.assertGreater(store.page_count, pages_before)   # 确实发生了分裂
        expect = reference_filter(
            [(k, k * 10) for k in list(range(0, 50)) + list(range(60, 77))], 0, 100)
        self.assertEqual(res.items, expect)                  # 新键也被扫到
        keys = [k for k, _ in res.items]
        self.assertEqual(len(keys), len(set(keys)))          # 无重复
        self.assertEqual(res.skipped, [])

    def test_split_of_page_being_read(self):
        # 钩子在读页快照之前触发：被读的页本身先分裂，扫描沿新链继续
        tree, store = build_tree(range(0, 50, 2), max_keys=8)  # 0,2,...,48
        target = leaf_id_of(tree, 20)
        fired = {"done": False}

        def hook(pid):
            if not fired["done"] and pid == target:
                fired["done"] = True
                for k in (21, 23, 25, 27):   # 属于 target 页自身的键域 -> 它先分裂
                    tree.insert(k, k * 10)

        store.on_read = hook
        res = tree.range_scan(0, 50)
        store.on_read = None

        self.assertTrue(fired["done"])
        expect = reference_filter(
            [(k, k * 10) for k in list(range(0, 50, 2)) + [21, 23, 25, 27]], 0, 50)
        self.assertEqual(res.items, expect)
        keys = [k for k, _ in res.items]
        self.assertEqual(len(keys), len(set(keys)))          # 分裂快照下无重复

    def test_split_plus_unreadable_page(self):
        # 分裂与不可读页同时发生：坏页被跳过，其余结果仍与全量过滤一致
        tree, store = build_tree(range(0, 50), max_keys=8)
        bad = leaf_id_of(tree, 30)
        lost = list(store.get_for_write(bad).keys)
        store.bad_pages.add(bad)
        fired = {"done": False}

        def hook(pid):
            if not fired["done"] and pid == leaf_id_of(tree, 8):
                fired["done"] = True
                for k in range(60, 70):
                    tree.insert(k, k * 10)

        store.on_read = hook
        res = tree.range_scan(0, 100)
        store.on_read = None

        expect = reference_filter(
            [(k, k * 10) for k in list(range(0, 50)) + list(range(60, 70))
             if k not in lost], 0, 100)
        self.assertEqual(res.items, expect)
        self.assertEqual(res.skipped, [bad])


# ---------------------------------------------------------------------------
# 7. 对拍：固定断言数据 + 随机对拍
# ---------------------------------------------------------------------------
class DifferentialTest(unittest.TestCase):
    def test_golden(self):
        # 对拍数据来自 golden_data.json：
        # range_scan 与全量过滤参考实现必须同时等于文件中的期望键列表
        with open(GOLDEN_PATH, encoding="utf-8") as f:
            golden = json.load(f)
        keys = golden["keys"]
        pairs = [(k, k * 10) for k in keys]
        tree, _ = build_tree(keys, max_keys=golden["leaf_max_keys"])
        for case in golden["cases"]:
            lo, hi, expect_keys = case["lo"], case["hi"], case["expect_keys"]
            with self.subTest(lo=lo, hi=hi, note=case["note"]):
                expect = [(k, k * 10) for k in expect_keys]
                # 一侧：范围扫描
                self.assertEqual(tree.range_scan(lo, hi).items, expect)
                # 另一侧：全量过滤参考实现
                self.assertEqual(reference_filter(pairs, lo, hi), expect)

    def test_random_against_full_filter(self):
        rng = random.Random(20261004)
        for trial in range(300):
            n = rng.randint(0, 120)
            keys = rng.sample(range(0, 500), n)
            max_keys = rng.choice([3, 4, 8, 16])
            tree, store = build_tree(keys, max_keys=max_keys)
            lo = rng.randint(-10, 510)
            hi = rng.randint(-10, 510)

            # 随机腐蚀一部分叶子页
            corrupted = set()
            pid = tree.leftmost_leaf_id()
            all_pids = []
            while pid is not None:
                all_pids.append(pid)
                pid = store.get_for_write(pid).next
            for pid in all_pids:
                if rng.random() < 0.15:
                    corrupted.add(pid)
                    store.bad_pages.add(pid)

            pairs = [(k, k * 10) for k in keys]
            res = tree.range_scan(lo, hi)

            # 对拍：结果 == 全量过滤 去掉 落在被跳过页上的键
            expect = [
                (k, v) for k, v in reference_filter(pairs, lo, hi)
                if leaf_id_of(tree, k) not in corrupted
            ]
            self.assertEqual(res.items, expect,
                             msg="trial=%d lo=%r hi=%r" % (trial, lo, hi))
            # 跳过的页必须都被记录，且只记录真正不可读的页
            self.assertEqual(set(res.skipped), set(res.skipped))  # 无重复记录
            self.assertTrue(set(res.skipped) <= corrupted)
            # 无故障时两侧必须完全一致
            if not corrupted:
                self.assertEqual(res.items, reference_filter(pairs, lo, hi))
            # 结果本身有序且无重复
            got = [k for k, _ in res.items]
            self.assertEqual(got, sorted(set(got)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
