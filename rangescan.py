"""
聚簇 B+ 树叶子链上的范围扫描（标准库实现，无第三方依赖）。

模型
----
- 数据按主键聚簇存放在 B+ 树叶子页内，叶子页之间以 next 指针串成有序链表。
- 叶子页落在 PageStore（模拟磁盘页存储）中，内部节点常驻内存。
- 每次读页返回的是“读入缓冲区”的快照；存储故障通过 PageReadError 模拟。

边界语义
--------
range_scan(lo, hi) 采用 **左闭右开 [lo, hi)**：
    返回满足  lo <= key < hi  的全部主键。
    lo == hi 或 lo > hi 时为空区间，立即返回（不读任何数据页）。

容错
----
扫描沿叶子链 next 推进。某一页读不出来（PageReadError）时：
1. 记录该页 page_id 到 ScanResult.skipped；
2. 通过内部节点给出的“高位围栏键（fence）”重新从根下降，跳过该页，
   继续后面的扫描，绝不中断整个扫描；
3. 若不可读页是叶子链的最后一页（fence 为 +inf），记录后正常结束。
若某页在扫描期间发生分裂，扫描读到的是分裂前/后的快照，配合
“仅发出严格大于已发最后一个主键 (last_key)”的去重逻辑，保证：
    - 每个键至多出现一次；
    - 分裂不会使扫描漏读沿链可达页上的任何键。
"""

from bisect import bisect_left, bisect_right
from collections import namedtuple

__all__ = [
    "PageReadError",
    "PageStore",
    "LeafPage",
    "InternalNode",
    "BPlusTree",
    "ScanResult",
]


class PageReadError(Exception):
    """某一页暂时不可读（正在分裂、IO 故障等）。"""

    def __init__(self, page_id):
        super().__init__("page %r is unreadable" % (page_id,))
        self.page_id = page_id


class LeafPage:
    __slots__ = ("keys", "values", "next")

    def __init__(self):
        self.keys = []
        self.values = []
        self.next = None  # 下一个叶子页的 page_id，最后一页为 None

    def snapshot(self):
        page = LeafPage()
        page.keys = list(self.keys)
        page.values = list(self.values)
        page.next = self.next
        return page


class InternalNode:
    """内部节点：children[i] 子树中的键均 < keys[i]（B+ 树分隔键）。"""

    __slots__ = ("keys", "children")

    def __init__(self, keys, children):
        self.keys = keys
        self.children = children  # 元素为 InternalNode 或叶子页 page_id


class PageStore:
    """模拟磁盘：叶子页按 page_id 存取，可注入读故障与“读到一半”钩子。"""

    def __init__(self):
        self._pages = {}
        self._next_id = 1
        # 这些页在读时会抛 PageReadError，模拟暂时读不出来
        self.bad_pages = set()
        # 每次 read 返回快照前回调 fn(page_id)，测试用它在扫描途中制造分裂
        self.on_read = None
        # 观测用：记录成功/失败读取顺序
        self.read_log = []

    def alloc(self, page):
        page_id = self._next_id
        self._next_id += 1
        self._pages[page_id] = page
        return page_id

    def read(self, page_id):
        """对外读接口：返回快照，可能注入故障。扫描只走这个接口。"""
        if self.on_read is not None:
            self.on_read(page_id)
        if page_id in self.bad_pages:
            self.read_log.append(("error", page_id))
            raise PageReadError(page_id)
        self.read_log.append(("ok", page_id))
        return self._pages[page_id].snapshot()

    # --- 以下供树自身（插入/分裂）使用，绕过故障注入与快照 ----------

    def get_for_write(self, page_id):
        return self._pages[page_id]

    @property
    def page_count(self):
        return len(self._pages)


ScanResult = namedtuple("ScanResult", ["items", "skipped"])


class BPlusTree:
    def __init__(self, store=None, max_keys=8):
        self.store = store if store is not None else PageStore()
        self.max_keys = max_keys
        first = self.store.alloc(LeafPage())
        self.root = first  # 根可能是叶子 page_id，也可能是 InternalNode

    # ---------- 插入（含叶子/内部节点分裂） ----------

    def insert(self, key, value):
        path = []  # [(InternalNode, child_index), ...]
        node = self.root
        while isinstance(node, InternalNode):
            idx = bisect_right(node.keys, key)
            path.append((node, idx))
            node = node.children[idx]
        leaf_id = node
        leaf = self.store.get_for_write(leaf_id)

        pos = bisect_left(leaf.keys, key)
        if pos < len(leaf.keys) and leaf.keys[pos] == key:
            leaf.values[pos] = value  # 主键唯一：覆盖
            return
        leaf.keys.insert(pos, key)
        leaf.values.insert(pos, value)

        if len(leaf.keys) <= self.max_keys:
            return

        mid = len(leaf.keys) // 2
        new_leaf = LeafPage()
        new_leaf.keys = leaf.keys[mid:]
        new_leaf.values = leaf.values[mid:]
        new_leaf.next = leaf.next
        leaf.keys = leaf.keys[:mid]
        leaf.values = leaf.values[:mid]
        new_id = self.store.alloc(new_leaf)
        leaf.next = new_id
        sep = new_leaf.keys[0]
        self._propagate_split(path, sep, new_id)

    def _propagate_split(self, path, sep_key, right_child):
        if not path:
            self.root = InternalNode([sep_key], [self.root, right_child])
            return
        parent, idx = path.pop()
        parent.keys.insert(idx, sep_key)
        parent.children.insert(idx + 1, right_child)
        if len(parent.keys) <= self.max_keys:
            return
        mid = len(parent.keys) // 2
        up_key = parent.keys[mid]
        right = InternalNode(
            parent.keys[mid + 1:],
            parent.children[mid + 1:],
        )
        parent.keys = parent.keys[:mid]
        parent.children = parent.children[: mid + 1]
        self._propagate_split(path, up_key, right)

    # ---------- 定位 ----------

    def _locate(self, key):
        """
        从根下降到“应包含 key”的叶子页。
        返回 (leaf_page_id, fence)：
          fence 是沿路径找到的最小内部隔键，严格大于该叶子页里的全部键；
          若该叶子是链上最后一页，fence 为 None（+inf）。
        """
        node = self.root
        fence = None
        while isinstance(node, InternalNode):
            idx = bisect_right(node.keys, key)
            if idx < len(node.keys):
                fence = node.keys[idx]
            node = node.children[idx]
        return node, fence

    def _fence_of(self, page_id, probe):
        """
        已知 page_id 不可读且手里没有它的 fence 时（沿链到达），
        反复从根下降以查出该页自身的 fence。fence 严格递增，必然收敛。
        """
        seen = set()
        while True:
            landed, fence = self._locate(probe)
            if landed == page_id:
                return fence
            if fence is None or landed in seen:
                return None
            seen.add(landed)
            probe = fence

    def leftmost_leaf_id(self):
        node = self.root
        while isinstance(node, InternalNode):
            node = node.children[0]
        return node

    # ---------- 全量扫描（调试/参考实现可用） ----------

    def full_scan(self):
        """沿叶子链从头到尾读一遍；遇不可读页同样跳过并记录。"""
        page_id = self.leftmost_leaf_id()
        items, skipped = [], []
        fence = None
        while page_id is not None:
            try:
                page = self.store.read(page_id)
            except PageReadError:
                skipped.append(page_id)
                if fence is None:
                    probe = items[-1][0] if items else self._min_key()
                    fence = self._fence_of(page_id, probe)
                if fence is None:
                    break
                page_id, fence = self._locate(fence)
                continue
            items.extend(zip(page.keys, page.values))
            page_id, fence = page.next, None
        return ScanResult(items, skipped)

    def _min_key(self):
        page = self.store.get_for_write(self.leftmost_leaf_id())
        return page.keys[0] if page.keys else 0

    # ---------- 范围扫描：主入口 ----------

    def range_scan(self, lo, hi):
        """
        返回 ScanResult：
          items   : [(key, value), ...]，满足 lo <= key < hi（左闭右开）
          skipped : 扫描途中读不出来、被跳过的叶子页 page_id（按遇到顺序）

        扫描严格沿叶子链 next 顺序推进；不可读页只跳过该页并记录，
        不影响其余页；扫描期间页发生分裂也不会产生重复或漏读。
        """
        items, skipped = [], []
        # 左闭右开：lo >= hi 是空区间
        if lo >= hi:
            return ScanResult(items, skipped)

        last_key = None  # 已发出的最大主键，用于分裂快照下的去重
        page_id, fence = self._locate(lo)

        while page_id is not None:
            try:
                page = self.store.read(page_id)  # 快照读，可能抛 PageReadError
            except PageReadError:
                skipped.append(page_id)
                if fence is None:
                    # 沿链到达、围栏未知：向根查询该页自己的围栏
                    probe = lo if last_key is None else last_key
                    fence = self._fence_of(page_id, probe)
                if fence is None:
                    # 最后一页也读不出来：后面不存在更多数据，记录后结束
                    break
                nxt, nxt_fence = self._locate(fence)  # 重新从根下降，越过坏页
                if nxt == page_id:
                    break  # 防御：围栏未推进，避免死循环
                page_id, fence = nxt, nxt_fence
                continue

            stop = False
            for key, value in zip(page.keys, page.values):
                if key >= hi:          # 右开：到 hi 立即停止
                    stop = True
                    break
                if key >= lo and (last_key is None or key > last_key):
                    items.append((key, value))
                    last_key = key
            if stop:
                break

            nxt = page.next            # 沿叶子链顺序推进
            if nxt is None:
                break
            page_id, fence = nxt, None  # 链上直达；若该页不可读再向根查围栏

        return ScanResult(items, skipped)
