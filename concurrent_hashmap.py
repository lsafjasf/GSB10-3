"""并发哈希表：分批增量扩容（仅 Python 3 标准库）。

设计要点
--------
* 任意时刻最多存在「旧表 + 新表」两个区域（``_state = (old, cur)`` 单引用
  原子替换，读者一次读出一对一致快照，不会读到撕裂状态）。
* 扩容采用增量迁移：后台迁移者按桶分批搬运（``migrate_some``），写线程
  也会顺手迁移自己命中的那个旧桶（写前保证本键旧桶已迁完）。
* 迁移期间查询始终覆盖新旧两个区域，保证不丢键。
* 键的移动是「旧桶 pop -> 新桶 append」，且全程持有旧桶锁，因此任意时刻
  一个键只存在于一个桶中，绝不出现两份；读者读旧桶需要同一把锁，
  因此也看不到 pop/append 之间的空窗。
* 同一时刻只允许一个迁移者（``_migration_lock`` 非阻塞试锁），抢不到锁的
  线程立即返回继续自己的读写，不会整体阻塞。
* 中断恢复：迁移在任意两个键移动之间被中断（异常、取消）都是一致状态
  ——已搬走的键在新表，未搬走的留在旧桶，``_cursor``/``migrated`` 标记
  记录断点，之后调用 ``migrate_some``/``migrate_all`` 即可从断点继续。

锁顺序（避免死锁）：旧桶锁 -> 新桶锁 -> 元数据锁 ``_mu``。
"""

import threading
import time

_LOAD_FACTOR = 0.75

_SENTINEL = object()


class MigrationInterrupted(RuntimeError):
    """迁移被中断（测试注入或外部取消）。

    抛出时表仍处于一致状态：每个键要么在旧桶、要么已在新桶，
    释放所有桶锁后可安全地继续迁移。
    """


def _find(bucket, key):
    for i, (k, _) in enumerate(bucket):
        if k == key:
            return i
    return -1


class _Table:
    __slots__ = ("buckets", "locks", "migrated")

    def __init__(self, capacity):
        self.buckets = [[] for _ in range(capacity)]  # 每桶为 [key, value] 列表
        self.locks = [threading.Lock() for _ in range(capacity)]
        self.migrated = None  # 成为旧表时初始化为每桶一个 bool

    @property
    def capacity(self):
        return len(self.buckets)


class ConcurrentHashMap:
    """支持并发读写与增量扩容的哈希表。"""

    def __init__(self, capacity=8, batch_size=2):
        capacity = max(2, capacity)
        self._state = (None, _Table(capacity))  # (old, cur)
        self._cursor = 0                        # 迁移者扫描旧桶的游标
        self._size = 0
        self._batch = max(1, batch_size)        # 每批最多迁移的桶数
        self._mu = threading.Lock()             # 结构元数据 / size
        self._migration_lock = threading.Lock()  # 保证同一时刻唯一迁移者
        # 自测观测用：每次 migrate_some 记录 (本批迁移桶数, 剩余未迁移桶数)
        self.batch_log = []
        # 测试注入：迁移 N 个键后抛 MigrationInterrupted（None 表示不注入）
        self.fail_after_moves = None

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get(self, key, default=None):
        """查询。迁移期间同时覆盖新旧两个区域，保证不丢键。"""
        while True:
            old, cur = self._state
            ci = hash(key) % cur.capacity
            with cur.locks[ci]:
                i = _find(cur.buckets[ci], key)
                if i >= 0:
                    return cur.buckets[ci][i][1]
            if old is None:
                return default  # 无迁移，新表即权威
            oi = hash(key) % old.capacity
            if old.migrated[oi]:
                # 旧桶已迁走：迁移完成于 flag 置位之前，重读一次新表即权威
                with cur.locks[ci]:
                    i = _find(cur.buckets[ci], key)
                    if i >= 0:
                        return cur.buckets[ci][i][1]
                so, sc = self._state
                if so is old and sc is cur and old.migrated[oi]:
                    return default
                continue  # 期间又发生了扩容/迁移完成，重试收敛
            with old.locks[oi]:
                if old.migrated[oi]:
                    continue  # 等待期间本桶被迁走，重读新表
                i = _find(old.buckets[oi], key)
                if i >= 0:
                    return old.buckets[oi][i][1]
                return default  # 持锁读取，旧桶未迁移时新表不可能有旧键

    def __contains__(self, key):
        return self.get(key, _SENTINEL) is not _SENTINEL

    def __len__(self):
        with self._mu:
            return self._size

    def items(self):
        """返回键值快照（供测试在静默期使用）。"""
        old, cur = self._state
        out = {}
        for t in (old, cur):
            if t is None:
                continue
            for b in t.buckets:
                for k, v in b:
                    out[k] = v
        return out

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def put(self, key, value):
        while True:
            old, cur = self._state
            if old is not None:
                # 写前保证本键所属旧桶已迁移，之后键只可能在新表
                self._migrate_bucket(old, cur, hash(key) % old.capacity)
            ci = hash(key) % cur.capacity
            with cur.locks[ci]:
                so, sc = self._state
                if so is not old or sc is not cur:
                    continue  # 期间发生了扩容/迁移完成，重来
                i = _find(cur.buckets[ci], key)
                if i >= 0:
                    cur.buckets[ci][i][1] = value
                else:
                    cur.buckets[ci].append([key, value])
                    with self._mu:
                        self._size += 1
                break
        self._maybe_resize()

    def delete(self, key):
        """删除键，返回是否删除成功。"""
        while True:
            old, cur = self._state
            if old is not None:
                self._migrate_bucket(old, cur, hash(key) % old.capacity)
            ci = hash(key) % cur.capacity
            with cur.locks[ci]:
                so, sc = self._state
                if so is not old or sc is not cur:
                    continue
                i = _find(cur.buckets[ci], key)
                if i < 0:
                    return False
                cur.buckets[ci].pop(i)
                with self._mu:
                    self._size -= 1
                return True

    # ------------------------------------------------------------------
    # 扩容与迁移
    # ------------------------------------------------------------------
    def _resize_locked(self):
        old, cur = self._state
        if old is not None:
            return False  # 已有迁移在进行，任意时刻最多两个区域
        cur.migrated = [False] * cur.capacity
        self._cursor = 0
        self._state = (cur, _Table(cur.capacity * 2))
        return True

    def _maybe_resize(self):
        with self._mu:
            old, cur = self._state
            if old is not None or self._size < cur.capacity * _LOAD_FACTOR:
                return False
            return self._resize_locked()

    def start_resize(self):
        """强制开始一次扩容（若当前无迁移）。返回是否成功启动。"""
        with self._mu:
            return self._resize_locked()

    def _migrate_bucket(self, old, new, oi):
        """把旧表第 oi 桶整体搬到新表。幂等；可在任意键之间安全中断。"""
        if old.migrated[oi]:
            return
        with old.locks[oi]:
            if old.migrated[oi]:
                return
            so, sc = self._state
            if so is not old or sc is not new:
                return  # 状态已变（迁移完成或新一轮扩容），放弃
            bucket = old.buckets[oi]
            while bucket:
                k, v = bucket.pop()  # 先 pop 再 append：任意时刻键只在一处
                ni = hash(k) % new.capacity
                with new.locks[ni]:
                    new.buckets[ni].append([k, v])
                if self.fail_after_moves is not None:
                    self.fail_after_moves -= 1
                    if self.fail_after_moves <= 0:
                        raise MigrationInterrupted(
                            "迁移在移动键 %r 后被中断（注入）" % (k,))
            old.migrated[oi] = True

    def migrate_some(self):
        """迁移一批（至多 batch_size 个桶）。

        返回 None 表示已有另一个迁移者在工作（本调用未阻塞）；
        返回 True 表示仍有剩余桶；返回 False 表示迁移完成/无迁移。
        """
        if not self._migration_lock.acquire(blocking=False):
            return None
        try:
            moved = 0
            while moved < self._batch:
                old, cur = self._state
                if old is None:
                    return False
                with self._mu:
                    oi = self._cursor
                    if oi >= old.capacity:
                        if all(old.migrated):
                            # 全部桶均已迁移，结束本轮扩容
                            self._state = (None, cur)
                            self._cursor = 0
                            self.batch_log.append((moved, 0))
                            return False
                        # 有桶因中断/异常遗留未迁完：回退游标补齐
                        self._cursor = old.migrated.index(False)
                        oi = self._cursor
                    self._cursor += 1
                if not old.migrated[oi]:
                    self._migrate_bucket(old, cur, oi)
                    moved += 1
            old2, _ = self._state
            remaining = 0
            if old2 is not None:
                remaining = sum(1 for f in old2.migrated if not f)
            self.batch_log.append((moved, remaining))
            return old2 is not None
        finally:
            self._migration_lock.release()

    def migrate_all(self):
        """驱动迁移直到完成（供测试或专用迁移线程使用）。"""
        while True:
            r = self.migrate_some()
            if r is False:
                return
            if r is None:
                time.sleep(0.001)  # 另有迁移者在工作，稍等

    @property
    def migration_in_progress(self):
        return self._state[0] is not None

    # ------------------------------------------------------------------
    # 自测支撑
    # ------------------------------------------------------------------
    def check_invariants(self):
        """正确性断言：每键恰好出现一次；已迁移旧桶必为空；计数一致。"""
        old, cur = self._state
        seen = {}
        for t in (old, cur):
            if t is None:
                continue
            for b in t.buckets:
                for k, v in b:
                    assert k not in seen, "键 %r 同时出现两份" % (k,)
                    seen[k] = v
        with self._mu:
            assert len(seen) == self._size, (
                "计数不一致: size=%d 实际=%d" % (self._size, len(seen)))
        if old is not None:
            for oi, b in enumerate(old.buckets):
                if old.migrated[oi]:
                    assert not b, "旧桶 %d 已标记迁移但仍非空" % oi
        return seen
