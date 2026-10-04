"""Self tests for the leaf-chain range scanner.

Run:  python3 -m unittest discover -s tests -v
or:   python3 tests/test_rangescan.py
"""

import json
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rangescan import (BPlusTree, PageStore, PageReadError, ScanResult,  # noqa: E402
                       range_scan)

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data",
                         "oracle_data.json")


def make_tree(records, leaf_capacity=4):
    store = PageStore()
    tree = BPlusTree.build(store, records, leaf_capacity=leaf_capacity)
    return store, tree


def oracle_filter(records, start, end):
    """Reference side: brute-force full scan + half-open filter."""
    if start >= end:
        return []
    return [(k, v) for k, v in records if start <= k < end]


def assert_scan_matches_oracle(test, records, start, end,
                               store=None, tree=None, on_page_read=None,
                               expect_skipped_ids=None):
    if tree is None:
        store, tree = make_tree(records)
    res = range_scan(tree, store, start, end, on_page_read=on_page_read)

    expected = oracle_filter(records, start, end)
    got_keys = [k for k, _ in res.records]

    test.assertEqual(got_keys, sorted(got_keys), "records must stay sorted")
    test.assertEqual(len(got_keys), len(set(got_keys)),
                     "records must be unique (no duplicate across splits)")

    skipped_ids = [s.page_id for s in res.skipped]
    if expect_skipped_ids is not None:
        test.assertEqual(skipped_ids, expect_skipped_ids)

    # Keys covered by skipped page ranges are the only allowed "missing".
    skipped_key_cover = set()
    for s in res.skipped:
        skipped_key_cover.update(
            k for k, _ in records
            if (s.low_key is None or k >= s.low_key)
            and (s.high_key is None or k <= s.high_key)
            and start <= k < end)

    got_set = set(got_keys)
    expected_set = set(k for k, _ in expected)
    test.assertEqual(got_set - expected_set, set(),
                     "scanner returned keys outside the oracle range")
    missing = expected_set - got_set
    test.assertTrue(missing <= skipped_key_cover,
                    "missing keys %r must all come from skipped pages"
                    % sorted(missing))
    if not res.skipped:
        test.assertEqual(res.records, expected,
                         "no faults: scanner must equal the oracle exactly")
    return res


class OracleDataTests(unittest.TestCase):
    """Data-driven 对拍: scanner side vs full-filter side from oracle_data.json."""

    @classmethod
    def setUpClass(cls):
        with open(DATA_PATH, encoding="utf-8") as fh:
            cls.data = json.load(fh)
        cls.cap = cls.data["leaf_capacity"]
        cls.records = [tuple(r) for r in cls.data["records"]]

    def test_all_cases(self):
        for case in self.data["cases"]:
            with self.subTest(case=case["name"]):
                store = PageStore()
                tree = BPlusTree.build(store, self.records,
                                       leaf_capacity=self.cap)
                for pid in case["faulty_pages"]:
                    store.set_faulty(pid)
                res = range_scan(tree, store,
                                 case["start_key"], case["end_key"])
                self.assertEqual(
                    res.records,
                    [tuple(r) for r in case["expected_records"]],
                    "scanner side != full-filter side")
                self.assertEqual(
                    [s.page_id for s in res.skipped],
                    [s["page_id"] for s in case["expected_skipped"]])

    def test_dataset_itself_matches_independent_full_filter(self):
        """Guard: the golden data really equals an independent full filter."""
        for case in self.data["cases"]:
            with self.subTest(case=case["name"]):
                expected = oracle_filter(self.records,
                                         case["start_key"],
                                         case["end_key"])
                golden = set(k for k, _ in
                             [tuple(r) for r in case["expected_records"]])
                skipped_ids = set(case["faulty_pages"])
                expected_left = [k for k, _ in expected
                                 if not self._key_on_faulty(k, skipped_ids)]
                self.assertEqual(sorted(golden), expected_left)

    def _key_on_faulty(self, key, faulty_ids):
        store = PageStore()
        tree = BPlusTree.build(store, self.records, leaf_capacity=self.cap)
        for pid in faulty_ids:
            meta = store.page_meta(pid)
            if meta.low_key <= key <= meta.high_key:
                return True
        return False


class BoundarySemanticsTests(unittest.TestCase):
    """Boundary semantics: interval is [start, end), left-closed right-open."""

    def setUp(self):
        self.records = [(k, k) for k in range(1, 101)]
        self.store, self.tree = make_tree(self.records, leaf_capacity=7)

    def test_left_boundary_is_included(self):
        res = range_scan(self.tree, self.store, 10, 30)
        keys = [k for k, _ in res.records]
        self.assertIn(10, keys)

    def test_right_boundary_is_excluded(self):
        res = range_scan(self.tree, self.store, 10, 30)
        keys = [k for k, _ in res.records]
        self.assertNotIn(30, keys)
        self.assertEqual(keys, list(range(10, 30)))

    def test_boundaries_land_exactly_on_page_separators(self):
        # capacity 7 -> leaf boundaries at 1,8,15,22,29,...; start/end there.
        res = range_scan(self.tree, self.store, 8, 29)
        self.assertEqual([k for k, _ in res.records], list(range(8, 29)))

    def test_single_record_interval(self):
        # [15,16) returns exactly one key.
        res = range_scan(self.tree, self.store, 15, 16)
        self.assertEqual([k for k, _ in res.records], [15])

    def test_key_between_existing_keys_uses_half_open_filter(self):
        res = range_scan(self.tree, self.store, 10, 11)
        self.assertEqual(res.records, [(10, 10)])


class EmptyRangeTests(unittest.TestCase):

    def setUp(self):
        self.records = [(k, k) for k in range(1, 51)]

    def test_equal_bounds_empty(self):
        store, tree = make_tree(self.records)
        res = range_scan(tree, store, 10, 10)
        self.assertEqual(res.records, [])
        self.assertEqual(res.skipped, [])

    def test_inverted_bounds_empty(self):
        store, tree = make_tree(self.records)
        res = range_scan(tree, store, 30, 5)
        self.assertEqual(res.records, [])

    def test_range_beyond_all_keys_empty(self):
        store, tree = make_tree(self.records)
        res = range_scan(tree, store, 1000, 2000)
        self.assertEqual(res.records, [])

    def test_range_before_all_keys_empty(self):
        store, tree = make_tree(self.records)
        res = range_scan(tree, store, -100, 0)
        self.assertEqual(res.records, [])

    def test_empty_tree(self):
        store, tree = make_tree([])
        res = range_scan(tree, store, 0, 100)
        self.assertEqual(res.records, [])


class SinglePageTests(unittest.TestCase):

    def test_range_within_one_leaf(self):
        records = [(k, k * 2) for k in range(1, 51)]
        assert_scan_matches_oracle(self, records, 17, 20)

    def test_exactly_one_full_page(self):
        records = [(k, k) for k in range(1, 51)]
        assert_scan_matches_oracle(self, records, 9, 13)


class MultiPageTests(unittest.TestCase):

    def test_range_spanning_all_pages(self):
        records = [(k, "d%d" % k) for k in range(1, 61)]
        assert_scan_matches_oracle(self, records, 1, 61)

    def test_range_spanning_several_pages(self):
        records = [(k, k) for k in range(1, 101)]
        assert_scan_matches_oracle(self, records, 3, 87)

    def test_scan_follows_leaf_chain_order(self):
        records = [(k, k) for k in range(1, 41)]
        store, tree = make_tree(records, leaf_capacity=3)
        res = range_scan(tree, store, 1, 41)
        self.assertEqual([k for k, _ in res.records], list(range(1, 41)))


class UnreadablePageTests(unittest.TestCase):

    def setUp(self):
        self.records = [(k, k) for k in range(1, 41)]

    def test_unreadable_page_is_skipped_and_recorded(self):
        store, tree = make_tree(self.records, leaf_capacity=4)
        bad = tree.leaf_ids[3]
        store.set_faulty(bad)
        res = range_scan(tree, store, 1, 41)
        self.assertEqual([s.page_id for s in res.skipped], [bad])
        skipped_meta = store.page_meta(bad)
        self.assertEqual(res.skipped[0].low_key, skipped_meta.low_key)
        self.assertEqual(res.skipped[0].high_key, skipped_meta.high_key)
        expected = [k for k in range(1, 41)
                    if not (skipped_meta.low_key <= k <= skipped_meta.high_key)]
        self.assertEqual([k for k, _ in res.records], expected)

    def test_scan_continues_after_fault(self):
        store, tree = make_tree(self.records, leaf_capacity=4)
        store.set_faulty(tree.leaf_ids[1])
        res = range_scan(tree, store, 1, 41)
        # keys after the faulty page are still delivered
        self.assertIn((40, 40), res.records)
        self.assertEqual(len(res.skipped), 1)

    def test_multiple_consecutive_faulty_pages(self):
        store, tree = make_tree(self.records, leaf_capacity=4)
        bad = tree.leaf_ids[2:5]
        for pid in bad:
            store.set_faulty(pid)
        res = range_scan(tree, store, 1, 41)
        self.assertEqual([s.page_id for s in res.skipped], bad)
        assert_scan_matches_oracle(self, self.records, 1, 41,
                                   store=store, tree=tree)

    def test_faulty_page_outside_range_not_visited(self):
        store, tree = make_tree(self.records, leaf_capacity=4)
        store.set_faulty(tree.leaf_ids[-1])  # last page
        res = range_scan(tree, store, 1, 5)  # first page only
        self.assertEqual(res.skipped, [])
        self.assertEqual([k for k, _ in res.records], list(range(1, 5)))


class MidScanSplitTests(unittest.TestCase):
    """A leaf page is split while the scanner is running."""

    def setUp(self):
        self.records = [(k, k) for k in range(1, 33)]  # cap 4 -> 8 leaves

    def test_current_page_split_after_it_was_read(self):
        store, tree = make_tree(self.records, leaf_capacity=4)
        split_pid = tree.leaf_ids[2]

        def hook(page_id):
            if page_id == split_pid:
                tree.split_leaf(split_pid)

        # All records still exist after the split; a split only rewires
        # pages, never deletes keys, so the scanner must equal the oracle.
        res = range_scan(tree, store, 1, 33, on_page_read=hook)
        keys = [k for k, _ in res.records]
        self.assertEqual(sorted(keys), keys)
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(keys, list(range(1, 33)))

    def test_future_page_split_before_it_is_reached(self):
        store, tree = make_tree(self.records, leaf_capacity=4)
        future_pid = tree.leaf_ids[5]

        def hook(page_id):
            if page_id == tree.leaf_ids[1]:
                tree.split_leaf(future_pid)  # split a page ahead of us

        res = range_scan(tree, store, 1, 33, on_page_read=hook)
        self.assertEqual([k for k, _ in res.records], list(range(1, 33)))

    def test_split_on_first_page_of_partial_range(self):
        store, tree = make_tree(self.records, leaf_capacity=4)
        first_pid = tree.find_leaf(10)
        new_pid = {"value": None}

        def hook(page_id):
            if page_id == first_pid and new_pid["value"] is None:
                new_pid["value"] = tree.split_leaf(first_pid)

        res = range_scan(tree, store, 10, 25, on_page_read=hook)
        self.assertEqual([k for k, _ in res.records], list(range(10, 25)))

    def test_split_plus_faulty_page_still_matches_oracle(self):
        store, tree = make_tree(self.records, leaf_capacity=4)
        split_pid = tree.leaf_ids[3]
        bad = tree.leaf_ids[6]

        def hook(page_id):
            if page_id == split_pid:
                tree.split_leaf(split_pid)

        store.set_faulty(bad)
        assert_scan_matches_oracle(self, self.records, 1, 33,
                                   store=store, tree=tree,
                                   on_page_read=hook,
                                   expect_skipped_ids=[bad])


class RandomizedFuzzTests(unittest.TestCase):
    """Random 对拍: random data/ranges/faults, reconciliation every time."""

    def test_random(self):
        rng = random.Random(20261004)
        for trial in range(200):
            n = rng.randint(0, 120)
            keys = rng.sample(range(-200, 200), n) if n else []
            records = [(k, "x%d" % k) for k in sorted(keys)]
            cap = rng.randint(1, 9)
            store, tree = make_tree(records, leaf_capacity=cap)

            # Random faulty pages.
            if tree.leaf_ids:
                for pid in tree.leaf_ids:
                    if rng.random() < 0.2:
                        store.set_faulty(pid)

            lo = rng.randint(-220, 200)
            span = rng.randint(0, 120)
            start, end = lo, lo + span

            def on_read(page_id, rng=rng):
                # Occasionally split the page just visited.
                if (tree.leaf_ids
                        and page_id in tree.leaf_ids
                        and len(store.pages[page_id].keys) >= 2
                        and rng.random() < 0.15):
                    tree.split_leaf(page_id)

            assert_scan_matches_oracle(self, records, start, end,
                                       store=store, tree=tree,
                                       on_page_read=on_read)


if __name__ == "__main__":
    unittest.main(verbosity=2)
