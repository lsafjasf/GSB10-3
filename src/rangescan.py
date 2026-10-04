"""Clustered primary-key range scan over a B+ tree leaf chain.

Model
-----
- Records are clustered by primary key inside leaf pages.
- Leaf pages are linked into a singly linked chain (``next_id``), which is
  the order a range scan follows.
- A page directory (``PageStore.meta``) holds per-page metadata
  (low key, high key, next pointer). The directory is always readable,
  even when the page *body* cannot be read (e.g. the page is being
  split or the IO layer transiently fails). This mirrors real systems
  where the page directory / buffer-frame headers survive while a page
  body read faults.

Range-scan semantics
--------------------
``range_scan(tree, store, start_key, end_key)`` scans the half-open
interval **[start_key, end_key)**: left-closed, right-open.

- ``start_key >= end_key`` yields an empty result without touching any page.
- A page whose body cannot be read is *skipped*: it is recorded in
  ``ScanResult.skipped`` and the scan continues with the next page in
  the chain. The scan never aborts because of an unreadable page.
- A monotonic watermark (last emitted key) guarantees that a leaf split
  happening mid-scan never produces duplicates or goes backwards.
"""

from bisect import bisect_right
from dataclasses import dataclass, field


class PageReadError(Exception):
    """Raised when a page body is temporarily unreadable."""


@dataclass
class Page:
    page_id: int
    is_leaf: bool
    keys: list
    values: list = None        # leaf only
    children: list = None      # internal only: page ids, len(keys) + 1
    next_id: int = None        # leaf only: next leaf in the chain

    def __post_init__(self):
        if self.is_leaf and self.values is None:
            self.values = []
        if not self.is_leaf and self.children is None:
            self.children = []


@dataclass
class PageMeta:
    page_id: int
    low_key: object      # smallest key the page may hold (inclusive)
    high_key: object     # largest key the page may hold (inclusive); None = +inf
    next_id: int         # next leaf page id in the chain, None = last


class PageStore:
    """Simulated disk: page bodies may fault, the directory never does."""

    def __init__(self):
        self.pages = {}
        self.meta = {}
        self.faulty = set()    # page ids whose body read raises PageReadError

    def read_page(self, page_id):
        if page_id in self.faulty:
            raise PageReadError("page %d temporarily unreadable" % page_id)
        return self.pages[page_id]

    def page_meta(self, page_id):
        return self.meta[page_id]

    def set_faulty(self, page_id, on=True):
        if on:
            self.faulty.add(page_id)
        else:
            self.faulty.discard(page_id)


class BPlusTree:
    """B+ tree over a PageStore. Leaf level is the clustered record chain."""

    def __init__(self, store, leaf_capacity=8, internal_capacity=8):
        self.store = store
        self.leaf_capacity = leaf_capacity
        self.internal_capacity = internal_capacity
        self.root_id = None
        self.leaf_ids = []
        self._next_page_id = 1

    def _alloc_id(self):
        pid = self._next_page_id
        self._next_page_id += 1
        return pid

    @classmethod
    def build(cls, store, records, leaf_capacity=8, internal_capacity=8):
        """Build a tree from sorted unique-key ``records`` [(key, value), ...]."""
        tree = cls(store, leaf_capacity, internal_capacity)
        leaves = []
        for i in range(0, max(len(records), 1), leaf_capacity):
            chunk = records[i:i + leaf_capacity]
            if not chunk:
                break
            leaf = Page(tree._alloc_id(), True,
                        [k for k, _ in chunk], [v for _, v in chunk])
            store.pages[leaf.page_id] = leaf
            leaves.append(leaf)
        tree._relink(leaves)
        return tree

    def _relink(self, leaves):
        """Recompute the leaf chain, the page directory and internal levels."""
        store = self.store
        for i, leaf in enumerate(leaves):
            leaf.next_id = leaves[i + 1].page_id if i + 1 < len(leaves) else None
        self.leaf_ids = [leaf.page_id for leaf in leaves]

        # Directory entries for leaves. high_key of a leaf is the key just
        # below the next leaf's low key; we store low keys and derive ranges.
        for i, leaf in enumerate(leaves):
            low = leaf.keys[0] if leaf.keys else None
            high = leaf.keys[-1] if leaf.keys else None
            store.meta[leaf.page_id] = PageMeta(leaf.page_id, low, high,
                                                leaf.next_id)

        # Rebuild internal levels bottom-up. Old internal pages are dropped.
        store.pages = {pid: p for pid, p in store.pages.items() if p.is_leaf}
        level = leaves
        while len(level) > 1:
            upper = []
            for i in range(0, len(level), self.internal_capacity):
                group = level[i:i + self.internal_capacity]
                seps = [self._low_key(p) for p in group[1:]]
                node = Page(self._alloc_id(), False, seps,
                            children=[p.page_id for p in group])
                store.pages[node.page_id] = node
                upper.append(node)
            level = upper
        self.root_id = level[0].page_id if level else None

    def _low_key(self, page):
        if page.is_leaf:
            return page.keys[0]
        return self._low_key(self.store.pages[page.children[0]])

    def find_leaf(self, key):
        """Descend from the root to the leaf that may contain ``key``."""
        pid = self.root_id
        while True:
            page = self.store.pages[pid]
            if page.is_leaf:
                return pid
            pid = page.children[bisect_right(page.keys, key)]

    def split_leaf(self, page_id):
        """Split a leaf in two, as concurrent inserts would. Returns new id."""
        idx = self.leaf_ids.index(page_id)
        old = self.store.pages[page_id]
        mid = len(old.keys) // 2
        new = Page(self._alloc_id(), True, old.keys[mid:], old.values[mid:])
        old.keys, old.values = old.keys[:mid], old.values[:mid]
        self.store.pages[new.page_id] = new
        leaves = [self.store.pages[pid] for pid in self.leaf_ids]
        leaves.insert(idx + 1, new)
        self._relink(leaves)
        return new.page_id


@dataclass
class SkippedPage:
    page_id: int
    low_key: object
    high_key: object


@dataclass
class ScanResult:
    records: list = field(default_factory=list)   # [(key, value), ...] in key order
    skipped: list = field(default_factory=list)   # [SkippedPage, ...] in visit order


def range_scan(tree, store, start_key, end_key, on_page_read=None):
    """Scan the half-open interval [start_key, end_key) along the leaf chain.

    Unreadable pages are skipped and recorded, never fatal. ``on_page_read``
    is an optional test hook invoked with the page id right after a page
    body is read (used to inject splits mid-scan).
    """
    result = ScanResult()
    if start_key >= end_key or tree.root_id is None:
        return result

    current = tree.find_leaf(start_key)
    last_key = None  # watermark: only emit keys strictly greater
    while current is not None:
        meta = store.page_meta(current)
        if meta.low_key is not None and meta.low_key >= end_key:
            break
        try:
            page = store.read_page(current)
        except PageReadError:
            result.skipped.append(SkippedPage(current, meta.low_key,
                                              meta.high_key))
        else:
            snapshot = list(zip(page.keys, page.values))
            if on_page_read is not None:
                on_page_read(current)
            for key, value in snapshot:
                if key < start_key or key >= end_key:
                    continue
                if last_key is not None and key <= last_key:
                    continue  # already emitted before a mid-scan split
                result.records.append((key, value))
                last_key = key
        # Re-read the directory entry: a split may have rewired the chain.
        current = store.page_meta(current).next_id
    return result
