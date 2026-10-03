"""索引膨胀检测与在线重建（仅标准库）。

模型：追加式日志索引（log-structured index）。每次 put/delete 追加一条日志，
delete 写入墓碑（tombstone）。长期增删后日志里堆积大量"死条目"：
  - 被覆盖的旧版本 put
  - 墓碑及其对应的 put
死条目占用空间、拖慢需要回放日志的操作（范围扫描、启动恢复、统计）。

膨胀度量：
  live_ratio = 有效条目数 / 日志总条目数
触发判据（可配置）：
  total_entries >= min_entries  且  live_ratio < max_live_ratio
默认 max_live_ratio = 0.5，即死条目超过一半才值得重建（理由见 README）。

在线重建流程：
  1. begin_rebuild()  在写锁内对活索引拍快照，并开启增量日志 delta
  2. 用快照构建紧凑的新索引（不持锁，不阻塞读写）
  3. commit_rebuild() 在写锁内回放 delta 追平 -> 影子对拍校验 -> 原子换指针
  4. 任何时刻失败可 abort_rebuild()，旧索引继续服务，可安全重试
"""

import threading
import time
from dataclasses import dataclass, field

# 每条日志条目的名义占用（字节），用于把条目数换算成空间占用。
ENTRY_SIZE = 64

PUT = "put"
DEL = "del"


class RebuildAborted(Exception):
    """重建被中断（取消 / 注入故障 / 对拍不一致）时抛出。"""


@dataclass
class BloatReport:
    live_entries: int
    total_entries: int
    dead_entries: int
    live_ratio: float
    space_bytes: int
    should_rebuild: bool
    reason: str


class LogIndex:
    """追加式日志索引：_log 是"磁盘占用"，_map 是查询用的哈希表。"""

    def __init__(self):
        self._log = []   # list[(op, key, value)]，只增不改，重建前持续膨胀
        self._map = {}   # key -> value，点查 O(1)

    def put(self, key, value):
        self._log.append((PUT, key, value))
        self._map[key] = value

    def delete(self, key):
        self._log.append((DEL, key, None))
        self._map.pop(key, None)

    def get(self, key, default=None):
        return self._map.get(key, default)

    def items(self):
        return dict(self._map)

    def scan(self, prefix=""):
        """范围扫描：必须回放整条日志再过滤，代价与总条目数成正比。

        膨胀越严重，scan 越慢——这是膨胀影响查询的直接体现。
        """
        merged = {}
        for op, key, value in self._log:
            if op == PUT:
                merged[key] = value
            else:
                merged.pop(key, None)
        return {k: v for k, v in merged.items() if k.startswith(prefix)}

    @property
    def total_entries(self):
        return len(self._log)

    @property
    def live_entries(self):
        return len(self._map)

    def measure(self, min_entries=1000, max_live_ratio=0.5):
        total = self.total_entries
        live = self.live_entries
        ratio = live / total if total else 1.0
        if total < min_entries:
            should, reason = False, "日志规模太小，重建收益抵不上一次重写"
        elif ratio >= max_live_ratio:
            should, reason = False, "有效条目占比尚可，回收空间不足一半"
        else:
            should, reason = True, "死条目占比过高，重建可回收过半空间"
        return BloatReport(
            live_entries=live,
            total_entries=total,
            dead_entries=total - live,
            live_ratio=ratio,
            space_bytes=total * ENTRY_SIZE,
            should_rebuild=should,
            reason=reason,
        )


class IndexStore:
    """持有当前活跃索引指针，负责写路径、增量日志与原子切换。"""

    def __init__(self, index=None):
        self._active = index if index is not None else LogIndex()
        self._write_lock = threading.Lock()
        self._delta = None  # 重建进行中的增量日志；None 表示无重建

    # ---- 读写路径 ----
    def put(self, key, value):
        with self._write_lock:
            self._active.put(key, value)
            if self._delta is not None:
                self._delta.append((PUT, key, value))

    def delete(self, key):
        with self._write_lock:
            self._active.delete(key)
            if self._delta is not None:
                self._delta.append((DEL, key, None))

    def get(self, key, default=None):
        # CPython 中引用读取是原子的：读者要么看到旧索引，要么看到新索引，
        # 两者内容一致，不存在撕裂状态。
        return self._active.get(key, default)

    def scan(self, prefix=""):
        return self._active.scan(prefix)

    def active(self):
        return self._active

    # ---- 重建生命周期 ----
    def begin_rebuild(self):
        """拍快照并开启增量日志。快照在写锁内完成（一次 dict.copy，极快）。"""
        with self._write_lock:
            snapshot = self._active.items()
            self._delta = []
            return snapshot

    def commit_rebuild(self, new_index, verify=True):
        """在写锁内：回放 delta 追平 -> 影子对拍 -> 原子切换。

        写操作只在"回放剩余 delta + 换指针"这极短窗口内被阻塞。
        返回 (旧索引, 对拍结果 dict)。
        """
        with self._write_lock:
            for op, key, value in self._delta:
                if op == PUT:
                    new_index.put(key, value)
                else:
                    new_index.delete(key)
            self._delta = None

            check = {"checked_keys": 0, "mismatches": 0, "samples": []}
            if verify:
                old_map = self._active.items()
                new_map = new_index.items()
                check["checked_keys"] = len(old_map) + len(new_map)
                for key in set(old_map) | set(new_map):
                    if old_map.get(key) != new_map.get(key):
                        check["mismatches"] += 1
                        if len(check["samples"]) < 5:
                            check["samples"].append(key)
                if check["mismatches"]:
                    raise RebuildAborted(
                        "影子对拍发现 %d 处不一致，放弃切换" % check["mismatches"]
                    )

            old = self._active
            self._active = new_index  # 原子切换：一条引用赋值
            return old, check

    def abort_rebuild(self):
        """中断重建：丢弃增量日志，旧索引继续服务，不丢任何数据。"""
        with self._write_lock:
            self._delta = None


@dataclass
class RebuildReport:
    status: str                       # "switched" / "aborted"
    checked_keys: int = 0
    mismatches: int = 0
    space_before_bytes: int = 0
    space_after_bytes: int = 0
    scan_ms_before: float = 0.0
    scan_ms_after: float = 0.0
    detail: str = ""


class OnlineRebuilder:
    """在线重建编排：快照 -> 构建 -> 追平 -> 对拍 -> 原子切换。"""

    def __init__(self, store, verify=True):
        self._store = store
        self._verify = verify

    def rebuild(self, fail_after_puts=None):
        """执行一次在线重建。

        fail_after_puts: 测试用故障注入——构建新索引写入 N 条后模拟崩溃。
        返回 RebuildReport；被中断时抛 RebuildAborted。
        """
        store = self._store
        old = store.active()
        space_before = old.measure().space_bytes
        scan_before = _time_scan(old)

        snapshot = store.begin_rebuild()
        new_index = LogIndex()
        try:
            for i, (key, value) in enumerate(snapshot.items()):
                new_index.put(key, value)  # 每个有效 key 只留一条，完成压缩
                if fail_after_puts is not None and i + 1 >= fail_after_puts:
                    raise RebuildAborted("注入故障：构建到第 %d 条时崩溃" % (i + 1))
        except BaseException:
            store.abort_rebuild()
            raise

        old, check = store.commit_rebuild(new_index, verify=self._verify)
        scan_after = _time_scan(store.active())
        return RebuildReport(
            status="switched",
            checked_keys=check["checked_keys"],
            mismatches=check["mismatches"],
            space_before_bytes=space_before,
            space_after_bytes=new_index.measure().space_bytes,
            scan_ms_before=scan_before,
            scan_ms_after=scan_after,
        )


def _time_scan(index, rounds=3):
    """测量 scan 耗时（毫秒），取多轮最小值降低噪声。"""
    best = float("inf")
    for _ in range(rounds):
        start = time.perf_counter()
        index.scan()
        best = min(best, (time.perf_counter() - start) * 1000.0)
    return best
