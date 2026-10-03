"""lsm.py — 分层合并（leveled compaction）LSM 风格存储引擎，仅依赖标准库。

设计要点
========
- 写入先进入内存表（MemTable），条目数达到 memtable_max_entries 后排序刷盘为 L0 的一个 run（SSTable 文件）。
- L0 内各 run 的键区间允许重叠；L1 及以上每层是一组键区间互不重叠的有序 run。
- 合并（compaction）触发条件：
    * L0：run 数量达到 l0_compaction_trigger 时，取最老的 run，
      连同 L0 中与之键区间重叠的 run、以及 L1 中与之键区间重叠的 run 一起合并，输出到 L1。
    * Li（1 <= i < max_levels-1）：总条目数达到 level_capacity_base * size_ratio**i 时，
      按轮询指针取一个 run，与 L(i+1) 中键区间重叠的 run 合并，输出到 L(i+1)。
    * 最后一层（max_levels-1）只做输入，不再向下合并。
- 每个键带全局递增序号 seq；合并时对同一键只保留 seq 最大的记录（最新写入遮蔽旧值）。
- 删除写墓碑（tombstone）。墓碑在合并到非最后一层时必须保留（下层可能还有旧值）；
  只有在最后一层参与合并时墓碑才能被真正丢弃。
- 写放大（WA）= 磁盘物理写字节数 / 用户逻辑写入字节数，引擎运行期实时统计。

SSTable 记录格式（小端）：
    u32 key_len | u32 value_len(0xFFFFFFFF 表示墓碑) | u64 seq | key | value
"""

import heapq
import os
import struct
import tempfile

MAGIC = b"LSM1"
TOMBSTONE = 0xFFFFFFFF
_HEADER = struct.Struct("<IIQ")
_U32 = struct.Struct("<I")


def _encode_record(key, value, seq):
    """序列化一条记录，返回字节串。value 为 None 表示墓碑。"""
    klen = len(key)
    if value is None:
        return _HEADER.pack(klen, TOMBSTONE, seq) + key
    return _HEADER.pack(klen, len(value), seq) + key + value


def _record_size(key, value):
    """一条记录序列化后的字节数（用于逻辑写入量统计）。"""
    vlen = 0 if value is None else len(value)
    return _HEADER.size + len(key) + vlen


class SSTableWriter:
    """顺序写入一个 run 文件（先写临时文件再原子改名）。"""

    def __init__(self, path):
        self.path = path
        self._tmp = path + ".tmp"
        self._fh = open(self._tmp, "wb")
        self._fh.write(MAGIC)
        self.bytes_written = len(MAGIC)
        self.count = 0
        self.min_key = None
        self.max_key = None

    def add(self, key, value, seq):
        data = _encode_record(key, value, seq)
        self._fh.write(data)
        self.bytes_written += len(data)
        self.count += 1
        if self.min_key is None:
            self.min_key = key
        self.max_key = key

    def close(self):
        self._fh.close()
        os.replace(self._tmp, self.path)


def _read_records(path):
    """顺序读取一个 run 文件，产出 (key, value, seq)。"""
    with open(path, "rb") as fh:
        magic = fh.read(4)
        if magic != MAGIC:
            raise ValueError("bad sstable magic: %r" % path)
        while True:
            header = fh.read(_HEADER.size)
            if not header:
                return
            klen, vlen, seq = _HEADER.unpack(header)
            key = fh.read(klen)
            if vlen == TOMBSTONE:
                yield key, None, seq
            else:
                yield key, fh.read(vlen), seq


def _iter_run(path):
    return _read_records(path)


def _merge_records(paths, drop_tombstones):
    """k 路归并多个有序 run，产出遮蔽后的 (key, value, seq)。

    同一 key 只保留 seq 最大的记录；drop_tombstones 为 True 时丢弃墓碑
    （仅在合并目标是最后一层时才允许）。
    """
    sources = [_read_records(p) for p in paths]
    heap = []
    for idx, it in enumerate(sources):
        try:
            key, value, seq = next(it)
            heapq.heappush(heap, (key, -seq, idx, value))
        except StopIteration:
            pass
    cur_key = None
    cur_value = None
    cur_seq = None
    while heap:
        key, neg_seq, idx, value = heapq.heappop(heap)
        seq = -neg_seq
        if key != cur_key:
            if cur_key is not None and not (drop_tombstones and cur_value is None):
                yield cur_key, cur_value, cur_seq
            cur_key, cur_value, cur_seq = key, value, seq
        try:
            nkey, nvalue, nseq = next(sources[idx])
            heapq.heappush(heap, (nkey, -nseq, idx, nvalue))
        except StopIteration:
            pass
    if cur_key is not None and not (drop_tombstones and cur_value is None):
        yield cur_key, cur_value, cur_seq


class Run:
    """磁盘上一个有序 run 的元信息。"""

    __slots__ = ("path", "count", "min_key", "max_key", "size")

    def __init__(self, path, count, min_key, max_key, size):
        self.path = path
        self.count = count
        self.min_key = min_key
        self.max_key = max_key
        self.size = size

    def overlaps(self, lo, hi):
        return self.min_key <= hi and lo <= self.max_key


def _ranges_overlap(a_lo, a_hi, b_lo, b_hi):
    return a_lo <= b_hi and b_lo <= a_hi


class LSMEngine:
    """分层合并存储引擎。

    参数
    ----
    path : 数据目录（会创建；目录内文件由引擎独占管理）
    memtable_max_entries : 内存表条目数上限，达到即刷盘为 L0 run
    l0_compaction_trigger : L0 run 数量达到该值即触发 L0->L1 合并
    max_levels : 总层数（含 L0），>= 2
    size_ratio : 相邻层容量比，Li(>=1) 容量 = level_capacity_base * size_ratio**i（条目数）
    level_capacity_base : 容量基数，默认等于 memtable_max_entries
    target_run_entries : 合并输出单个 run 的目标条目数（超过则切分多个 run）
    """

    def __init__(self, path, memtable_max_entries=1000, l0_compaction_trigger=4,
                 max_levels=4, size_ratio=10, level_capacity_base=None,
                 target_run_entries=None):
        if max_levels < 2:
            raise ValueError("max_levels must be >= 2")
        if l0_compaction_trigger < 2:
            raise ValueError("l0_compaction_trigger must be >= 2")
        self.path = path
        os.makedirs(path, exist_ok=True)
        self.memtable_max_entries = memtable_max_entries
        self.l0_compaction_trigger = l0_compaction_trigger
        self.max_levels = max_levels
        self.size_ratio = size_ratio
        self.level_capacity_base = (level_capacity_base
                                    if level_capacity_base is not None
                                    else memtable_max_entries)
        self.target_run_entries = (target_run_entries
                                   if target_run_entries is not None
                                   else memtable_max_entries)

        self._seq = 0
        self._memtable = {}          # key -> (value, seq)，value 为 None 表示墓碑
        self._levels = [[] for _ in range(max_levels)]  # 每层 Run 列表
        self._compact_ptr = [0] * max_levels            # Li 轮询选择指针
        self._file_counter = 0

        # 统计
        self.logical_bytes = 0       # 用户逻辑写入字节
        self.physical_bytes = 0      # 磁盘物理写入字节（刷盘 + 合并输出）
        self.flush_count = 0
        self.compaction_count = 0

    # ---------- 写路径 ----------

    def put(self, key, value):
        if isinstance(key, str):
            key = key.encode("utf-8")
        if isinstance(value, str):
            value = value.encode("utf-8")
        self._write(key, value)

    def delete(self, key):
        if isinstance(key, str):
            key = key.encode("utf-8")
        self._write(key, None)

    def _write(self, key, value):
        self._seq += 1
        self._memtable[key] = (value, self._seq)
        self.logical_bytes += _record_size(key, value)
        if len(self._memtable) >= self.memtable_max_entries:
            self._flush()

    def _next_file(self):
        self._file_counter += 1
        return os.path.join(self.path, "run-%06d.sst" % self._file_counter)

    def _flush(self):
        if not self._memtable:
            return
        writer = SSTableWriter(self._next_file())
        for key in sorted(self._memtable):
            value, seq = self._memtable[key]
            writer.add(key, value, seq)
        writer.close()
        self._levels[0].append(Run(writer.path, writer.count,
                                   writer.min_key, writer.max_key,
                                   writer.bytes_written))
        self.physical_bytes += writer.bytes_written
        self.flush_count += 1
        self._memtable = {}
        self._maybe_compact()

    # ---------- 合并 ----------

    def _level_capacity(self, level):
        """Li(i>=1) 的容量上限（条目数）。L0 以 run 数量计，不用此函数。"""
        return self.level_capacity_base * (self.size_ratio ** level)

    def _maybe_compact(self):
        while True:
            if len(self._levels[0]) >= self.l0_compaction_trigger:
                self._compact_l0()
                continue
            progressed = False
            for level in range(1, self.max_levels - 1):
                if sum(r.count for r in self._levels[level]) >= self._level_capacity(level):
                    self._compact_level(level)
                    progressed = True
                    break
            if not progressed:
                return

    def _compact_l0(self):
        """L0->L1：取最老 run，扩到 L0/L1 中所有键区间重叠的 run。"""
        l0 = self._levels[0]
        seed = l0[0]
        lo, hi = seed.min_key, seed.max_key
        inputs0 = [r for r in l0 if r.overlaps(lo, hi)]
        inputs1 = [r for r in self._levels[1] if r.overlaps(lo, hi)]
        self._do_compact(1, inputs0, inputs1)
        self._levels[0] = [r for r in l0 if r not in inputs0]

    def _compact_level(self, level):
        """Li->L(i+1)：轮询取一个 run，与下一层重叠 run 合并。"""
        runs = self._levels[level]
        idx = self._compact_ptr[level] % len(runs)
        self._compact_ptr[level] = idx + 1
        seed = runs[idx]
        inputs_next = [r for r in self._levels[level + 1]
                       if r.overlaps(seed.min_key, seed.max_key)]
        self._do_compact(level + 1, [seed], inputs_next)
        self._levels[level] = [r for r in runs if r is not seed]

    def _do_compact(self, to_level, inputs_a, inputs_b):
        """把 inputs_a、inputs_b 归并后写入 to_level（按 target_run_entries 切分）。"""
        drop_tombstones = (to_level == self.max_levels - 1)
        paths = [r.path for r in inputs_a] + [r.path for r in inputs_b]
        new_runs = []
        writer = None
        for key, value, seq in _merge_records(paths, drop_tombstones):
            if writer is None:
                writer = SSTableWriter(self._next_file())
            writer.add(key, value, seq)
            if writer.count >= self.target_run_entries:
                writer.close()
                new_runs.append(Run(writer.path, writer.count,
                                    writer.min_key, writer.max_key,
                                    writer.bytes_written))
                self.physical_bytes += writer.bytes_written
                writer = None
        if writer is not None:
            writer.close()
            new_runs.append(Run(writer.path, writer.count,
                                writer.min_key, writer.max_key,
                                writer.bytes_written))
            self.physical_bytes += writer.bytes_written

        # 输出与层内既有 run 保持键有序（输入键区间连续，直接按位置插入）
        existing = [r for r in self._levels[to_level] if r not in inputs_b]
        if new_runs:
            lo = new_runs[0].min_key
            pos = 0
            while pos < len(existing) and existing[pos].min_key < lo:
                pos += 1
            existing[pos:pos] = new_runs
        self._levels[to_level] = existing

        for r in inputs_a + inputs_b:
            os.unlink(r.path)
        self.compaction_count += 1

    # ---------- 读路径 ----------

    def get(self, key):
        """读取 key 的最新值；不存在或已删除返回 None。"""
        if isinstance(key, str):
            key = key.encode("utf-8")
        best_seq = -1
        best_value = None
        hit = False
        if key in self._memtable:
            value, seq = self._memtable[key]
            best_seq, best_value, hit = seq, value, True
        for level in self._levels:
            for run in level:
                if not (run.min_key <= key <= run.max_key):
                    continue
                for k, v, seq in _read_records(run.path):
                    if k == key and seq > best_seq:
                        best_seq, best_value, hit = seq, v, True
        return best_value if hit else None

    def scan(self):
        """按 key 升序产出全库遮蔽后的 (key, value)，跳过墓碑。"""
        items = dict(self._memtable)  # key -> (value, seq)
        for level in self._levels:
            for run in level:
                for k, v, seq in _read_records(run.path):
                    if k not in items or seq > items[k][1]:
                        items[k] = (v, seq)
        for key in sorted(items):
            value, _ = items[key]
            if value is not None:
                yield key, value

    # ---------- 观测 ----------

    def write_amplification(self):
        """写放大 = 物理写字节 / 逻辑写字节。"""
        if self.logical_bytes == 0:
            return 0.0
        return self.physical_bytes / self.logical_bytes

    def stats(self):
        return {
            "logical_bytes": self.logical_bytes,
            "physical_bytes": self.physical_bytes,
            "write_amplification": self.write_amplification(),
            "flush_count": self.flush_count,
            "compaction_count": self.compaction_count,
            "levels": [
                {"runs": len(runs), "entries": sum(r.count for r in runs)}
                for runs in self._levels
            ],
        }

    def level_entries(self, level):
        return sum(r.count for r in self._levels[level])

    def level_runs(self, level):
        return list(self._levels[level])

    def flush(self):
        """强制把内存表刷盘（测试与收尾用）。"""
        self._flush()

    def close(self):
        self._flush()
