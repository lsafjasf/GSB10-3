"""分层合并存储引擎（教学版 LSM-Tree，仅用 Python 3 标准库）。

结构：
    写入 -> MemTable(内存有序 dict)
          -> flush 为 L0 的一个有序 run 文件
          -> 某层字节数超过容量上限时，整层 merge 进下一层

遮蔽规则：
    读取顺序 MemTable -> L0(新 run 优先) -> L1 -> ...，
    第一个命中的记录决定结果；value 为 None 表示删除标记（tombstone）。
    合并进最后一层时删除标记会被丢弃（其下再无旧数据需要遮蔽）。
"""

import bisect
import json
import os
import uuid

TOMBSTONE = None  # value == None 即删除标记

MANIFEST_NAME = "MANIFEST.json"


class Run:
    """磁盘上的一个有序 run：JSON Lines，每行 [key, value]，按 key 升序。"""

    def __init__(self, dirpath, filename):
        self.dirpath = dirpath
        self.filename = filename
        self.path = os.path.join(dirpath, filename)

    def size_bytes(self):
        return os.path.getsize(self.path)

    def entries(self):
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                key, value = json.loads(line)
                yield key, value

    def lookup(self, key):
        """二分查找，返回 (found, value)；value 可能为 TOMBSTONE。"""
        keys = []
        values = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                k, v = json.loads(line)
                keys.append(k)
                values.append(v)
        idx = bisect.bisect_left(keys, key)
        if idx < len(keys) and keys[idx] == key:
            return True, values[idx]
        return False, None

    def destroy(self):
        if os.path.exists(self.path):
            os.remove(self.path)


def _encode_record(key, value):
    return (json.dumps([key, value], ensure_ascii=False) + "\n").encode("utf-8")


def _user_bytes(key, value):
    total = len(key.encode("utf-8"))
    if value is not TOMBSTONE:
        total += len(value.encode("utf-8"))
    return total


class LSMTree:
    """分层合并存储引擎。

    参数表（默认值面向真实使用；自测/实验里会缩小）：
        path                    数据目录（run 文件 + MANIFEST.json）
        memtable_max_entries    MemTable 条目数刷盘阈值，默认 1000
        num_levels              磁盘层级数（L0..L{num_levels-1}），默认 4
        level0_capacity_bytes   L0 容量上限（字节），默认 4 MiB
        fanout                  相邻层容量倍数，L_i 上限 =
                                level0_capacity_bytes * fanout**i，默认 10
    合并触发条件：某层现有 run 总字节数 > 该层容量上限时，
    把该层全部 run 与下一层全部 run 做有序归并，结果写成下一层的一个新 run；
    合并不止一层时自底向上级联（L0 -> L1 -> L2 ...）。
    """

    def __init__(
        self,
        path,
        memtable_max_entries=1000,
        num_levels=4,
        level0_capacity_bytes=4 * 1024 * 1024,
        fanout=10,
    ):
        if memtable_max_entries < 1:
            raise ValueError("memtable_max_entries 必须 >= 1")
        if num_levels < 1:
            raise ValueError("num_levels 必须 >= 1")
        if fanout < 1:
            raise ValueError("fanout 必须 >= 1")
        self.path = path
        os.makedirs(path, exist_ok=True)
        self.memtable_max_entries = memtable_max_entries
        self.num_levels = num_levels
        self.level0_capacity_bytes = level0_capacity_bytes
        self.fanout = fanout

        self.memtable = {}  # key -> value(或 TOMBSTONE)，dict 保持插入序
        self.levels = [[] for _ in range(num_levels)]

        # 写放大统计
        self.user_bytes_written = 0       # 用户写入的 key+value 字节
        self.disk_bytes_written = 0       # 所有 flush/merge 产物字节
        self.level_bytes_written = [0 for _ in range(num_levels)]

        self._load_manifest()

    # ---------- 参数 ----------

    def level_capacity(self, level):
        return self.level0_capacity_bytes * (self.fanout ** level)

    def level_size(self, level):
        return sum(run.size_bytes() for run in self.levels[level])

    def params(self):
        return {
            "memtable_max_entries": self.memtable_max_entries,
            "num_levels": self.num_levels,
            "level0_capacity_bytes": self.level0_capacity_bytes,
            "fanout": self.fanout,
            "level_capacities_bytes": [
                self.level_capacity(i) for i in range(self.num_levels)
            ],
        }

    def stats(self):
        wa = (
            self.disk_bytes_written / self.user_bytes_written
            if self.user_bytes_written
            else 0.0
        )
        return {
            "user_bytes": self.user_bytes_written,
            "disk_bytes": self.disk_bytes_written,
            "write_amplification": wa,
            "level_bytes_written": list(self.level_bytes_written),
            "level_sizes_bytes": [self.level_size(i) for i in range(self.num_levels)],
            "run_counts": [len(self.levels[i]) for i in range(self.num_levels)],
            "memtable_entries": len(self.memtable),
        }

    # ---------- 读 ----------

    def get(self, key):
        """返回最新值；不存在或被删除返回 None。"""
        if key in self.memtable:
            value = self.memtable[key]
            return None if value is TOMBSTONE else value
        for level in self.levels:
            # 每层中越晚 append 的 run 越新
            for run in reversed(level):
                found, value = run.lookup(key)
                if found:
                    return None if value is TOMBSTONE else value
        return None

    # ---------- 写 ----------

    def put(self, key, value):
        if value is None:
            raise ValueError("value 不能为 None；删除请用 delete()")
        if not isinstance(key, str) or not isinstance(value, str):
            raise TypeError("key/value 必须是 str")
        self.user_bytes_written += _user_bytes(key, value)
        self.memtable[key] = value
        self._maybe_flush_memtable()

    def delete(self, key):
        if not isinstance(key, str):
            raise TypeError("key 必须是 str")
        self.user_bytes_written += len(key.encode("utf-8"))
        self.memtable[key] = TOMBSTONE
        self._maybe_flush_memtable()

    def flush(self):
        """强制把 MemTable 刷成 L0 的一个 run。"""
        if not self.memtable:
            return
        run = self._write_run(0, sorted(self.memtable.items()))
        self.levels[0].append(run)
        self.memtable = {}
        self._save_manifest()
        self._maybe_compact()

    def close(self):
        self.flush()

    # ---------- 合并 ----------

    def _maybe_flush_memtable(self):
        if len(self.memtable) >= self.memtable_max_entries:
            self.flush()

    def _maybe_compact(self):
        # 从 L0 向深层检查；i 层合并进 i+1 后循环继续检查 i+1，实现级联
        for src in range(self.num_levels - 1):
            if self.level_size(src) > self.level_capacity(src):
                self._merge_levels(src, src + 1)

    def _merge_levels(self, src, dst):
        """src 层全部 run（新）与 dst 层全部 run（旧）归并为 dst 一个新 run。"""
        merged = {}
        # 旧层先放，再用新层逐条覆盖；层内按 run append 顺序（旧 -> 新）
        for run in self.levels[dst]:
            for key, value in run.entries():
                merged[key] = value
        for run in self.levels[src]:
            for key, value in run.entries():
                merged[key] = value

        # 合并进最后一层时 tombstone 已无旧数据可遮蔽，直接丢弃
        if dst == self.num_levels - 1:
            items = [(k, v) for k, v in merged.items() if v is not TOMBSTONE]
        else:
            items = list(merged.items())
        items.sort(key=lambda kv: kv[0])

        old_runs = list(self.levels[src]) + list(self.levels[dst])
        new_run = self._write_run(dst, items)
        for run in old_runs:
            run.destroy()
        self.levels[src] = []
        self.levels[dst] = [new_run]
        self._save_manifest()

    # ---------- run 文件 / manifest ----------

    def _write_run(self, level, items):
        filename = "run-L%d-%s.jsonl" % (level, uuid.uuid4().hex[:12])
        path = os.path.join(self.path, filename)
        payload = b"".join(_encode_record(k, v) for k, v in items)
        with open(path, "wb") as f:
            f.write(payload)
        self.disk_bytes_written += len(payload)
        self.level_bytes_written[level] += len(payload)
        return Run(self.path, filename)

    def _manifest_path(self):
        return os.path.join(self.path, MANIFEST_NAME)

    def _save_manifest(self):
        data = {
            "levels": [[run.filename for run in level] for level in self.levels],
            "user_bytes_written": self.user_bytes_written,
            "disk_bytes_written": self.disk_bytes_written,
            "level_bytes_written": self.level_bytes_written,
        }
        tmp = self._manifest_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, self._manifest_path())

    def _load_manifest(self):
        manifest = self._manifest_path()
        if not os.path.exists(manifest):
            return
        with open(manifest, "r", encoding="utf-8") as f:
            data = json.load(f)
        stored_levels = data.get("levels", [])
        for i, filenames in enumerate(stored_levels[: self.num_levels]):
            self.levels[i] = [Run(self.path, name) for name in filenames]
        self.user_bytes_written = data.get("user_bytes_written", 0)
        self.disk_bytes_written = data.get("disk_bytes_written", 0)
        saved = data.get("level_bytes_written", [])
        self.level_bytes_written = [
            saved[i] if i < len(saved) else 0 for i in range(self.num_levels)
        ]
