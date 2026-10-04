"""Generate data/oracle_data.json: fixed dataset + expected scan outputs.

Expected outputs are computed by independent full-filter logic (the
"oracle"), NOT by the leaf-chain scanner, so the test can 对拍 the two
sides: range_scan(...) == full_filter(...).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from rangescan import BPlusTree, PageStore, PageReadError  # noqa: E402

LEAF_CAPACITY = 4
RECORDS = [(k, "v%03d" % k) for k in range(1, 41)]  # keys 1..40, 10 leaf pages


def full_filter(records, start, end):
    """Oracle: plain full scan + filter on the half-open interval."""
    if start >= end:
        return []
    return [(k, v) for k, v in records if start <= k < end]


def build_store():
    store = PageStore()
    tree = BPlusTree.build(store, RECORDS, leaf_capacity=LEAF_CAPACITY)
    return store, tree


def leaf_key_ranges(store, tree):
    return {pid: (store.meta[pid].low_key, store.meta[pid].high_key)
            for pid in tree.leaf_ids}


def expected_with_faults(faulty_pids, start, end):
    """Expected scan output when some page bodies are unreadable.

    Records on faulty pages are absent from `records`; the faulty pages
    appear in `skipped`. Everything else must match the oracle exactly.
    """
    store, tree = build_store()
    ranges = leaf_key_ranges(store, tree)
    faulty_keys = set()
    skipped = []
    for pid in tree.leaf_ids:
        if pid not in faulty_pids:
            continue
        low, high = ranges[pid]
        if low >= end or high < start:
            continue  # page not on the scan path
        skipped.append({"page_id": pid, "low_key": low, "high_key": high})
        faulty_keys.update(k for k, _ in RECORDS if low <= k <= high)
    records = [(k, v) for k, v in full_filter(RECORDS, start, end)
               if k not in faulty_keys]
    return {"records": records, "skipped": skipped}


def main():
    store, tree = build_store()
    leaf_ids = list(tree.leaf_ids)

    cases = []

    def add(name, start, end, faulty=()):
        exp = expected_with_faults(set(faulty), start, end)
        cases.append({
            "name": name,
            "start_key": start,
            "end_key": end,
            "faulty_pages": list(faulty),
            "expected_records": exp["records"],
            "expected_skipped": exp["skipped"],
        })

    add("full_range", 1, 41)
    add("left_closed_right_open", 5, 18)          # includes 5, excludes 18
    add("single_page", 9, 13)                     # inside one leaf page
    add("multi_page", 3, 30)                      # spans many leaf pages
    add("empty_equal_bounds", 10, 10)
    add("empty_inverted_bounds", 20, 10)
    add("empty_gap", 41, 100)                     # beyond all keys
    add("skip_middle_page", 1, 41, faulty=[leaf_ids[4]])
    add("skip_first_page_on_path", 1, 41, faulty=[leaf_ids[0]])
    add("skip_two_pages", 5, 33, faulty=[leaf_ids[2], leaf_ids[6]])

    out = {
        "semantics": "half-open interval [start_key, end_key)",
        "leaf_capacity": LEAF_CAPACITY,
        "records": RECORDS,
        "cases": cases,
    }
    path = os.path.join(os.path.dirname(__file__), "..", "data",
                        "oracle_data.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    print("wrote", os.path.normpath(path), "with", len(cases), "cases")


if __name__ == "__main__":
    main()
