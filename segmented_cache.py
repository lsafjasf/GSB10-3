"""分段 LRU 缓存（线程安全，仅标准库）。

设计要点
--------
1. 键 -> 段 的映射使用 zlib.crc32（固定多项式、无随机盐），
   纯函数、与进程/平台无关，因此同一个键永远落在同一个段
   （映射稳定性）。
2. 容量上界按整表计算：全局占用计数器 `_size` 是唯一硬约束，
   保证任意时刻 sum(len(shard)) <= global_capacity。
   每段配额 quota 之和恰好等于 global_capacity（前 capacity % num_shards
   段多 1），但配额只是“公平份额”参考，不是硬上限——压力不均时
   热点段可以借用其他段未用满的容量，避免过早淘汰。
3. 淘汰在段内生效：优先淘汰目标段自己的 LRU 尾部；仅当目标段为空
   而整表已满的极端情形下，才淘汰“当前最大段”的 LRU 尾部，
   以维持全局容量上界。
4. 命中率按段统计并汇总出全局视角的命中率（见 stats()）。

锁序约定（防死锁）：段锁按段下标升序获取；_size_lock 永远在最内层。
"""

from __future__ import annotations

import threading
import zlib
from collections import OrderedDict

MASK32 = 0xFFFFFFFF


def stable_hash(key: str) -> int:
    """稳定 32 位哈希，基于 zlib.crc32（标准库）。

    crc32 是固定多项式的 CRC：不设随机种子，因此同一字符串在任意
    进程/解释器/平台上结果完全一致（不像内建 hash() 会按
    PYTHONHASHSEED 加盐），满足“同键必落同段”的稳定性要求。
    """
    if not isinstance(key, str):
        raise TypeError(f"key must be str, got {type(key).__name__}")
    return zlib.crc32(key.encode("utf-8")) & MASK32


def shard_index(key: str, num_shards: int) -> int:
    """键到段下标的稳定映射。"""
    if num_shards < 1:
        raise ValueError("num_shards must be >= 1")
    return stable_hash(key) % num_shards


def assert_mapping_stability(keys, num_shards: int, rounds: int = 3) -> None:
    """映射稳定性断言：同一批键重复映射 rounds 次，结果必须逐位一致。"""
    first = [shard_index(k, num_shards) for k in keys]
    for _ in range(rounds - 1):
        again = [shard_index(k, num_shards) for k in keys]
        assert again == first, "shard mapping is not stable"


class _Shard:
    """单个段：一把锁 + 一个按 LRU 序的 OrderedDict（头部=最久未用）。"""

    __slots__ = ("lock", "data", "quota", "hits", "misses")

    def __init__(self, quota: int):
        self.lock = threading.Lock()
        self.data: "OrderedDict[str, object]" = OrderedDict()
        self.quota = quota
        self.hits = 0
        self.misses = 0


class SegmentedCache:
    """分段 LRU 缓存。

    :param capacity: 整表容量上界（全局），任意时刻总条目数不超过它。
    :param num_shards: 段数。
    """

    def __init__(self, capacity: int, num_shards: int = 16):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        if num_shards < 1:
            raise ValueError("num_shards must be >= 1")
        self.capacity = capacity
        self.num_shards = num_shards
        base, extra = divmod(capacity, num_shards)
        # 前 extra 段配额多 1，保证 sum(quota) == capacity（恰好相等）
        self._shards = [
            _Shard(base + (1 if i < extra else 0)) for i in range(num_shards)
        ]
        self._size = 0  # 全局占用计数：唯一的容量硬约束
        self._size_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # 映射
    # ------------------------------------------------------------------ #
    def shard_for_key(self, key: str) -> int:
        return shard_index(key, self.num_shards)

    @property
    def quotas(self) -> list:
        """各段配额（公平份额参考值，非硬上限），总和恒等于 capacity。"""
        return [s.quota for s in self._shards]

    # ------------------------------------------------------------------ #
    # 全局容量计数（硬上界）
    # ------------------------------------------------------------------ #
    def _claim_slot(self) -> bool:
        with self._size_lock:
            if self._size < self.capacity:
                self._size += 1
                return True
            return False

    def _release_slot(self) -> None:
        with self._size_lock:
            self._size -= 1

    # ------------------------------------------------------------------ #
    # 读
    # ------------------------------------------------------------------ #
    def get(self, key: str, default=None):
        shard = self._shards[self.shard_for_key(key)]
        with shard.lock:
            if key in shard.data:
                shard.data.move_to_end(key)
                shard.hits += 1
                return shard.data[key]
            shard.misses += 1
            return default

    def __contains__(self, key: str) -> bool:
        shard = self._shards[self.shard_for_key(key)]
        with shard.lock:
            return key in shard.data

    def __len__(self) -> int:
        with self._size_lock:
            return self._size

    # ------------------------------------------------------------------ #
    # 写
    # ------------------------------------------------------------------ #
    def set(self, key: str, value) -> None:
        idx = self.shard_for_key(key)
        shard = self._shards[idx]
        with shard.lock:
            if key in shard.data:  # 更新已存在的键：不占新容量
                shard.data[key] = value
                shard.data.move_to_end(key)
                return
            if self._claim_slot():
                shard.data[key] = value
                return
            # 整表已满：优先在目标段内淘汰（段内 LRU）
            if shard.data:
                shard.data.popitem(last=False)
                shard.data[key] = value
                return
        # 极端情形：目标段为空但整表已满 -> 从当前最大段淘汰其 LRU 尾部
        self._insert_after_remote_eviction(idx, key, value)

    def _insert_after_remote_eviction(self, idx: int, key: str, value) -> None:
        """目标段为空且全局已满时的兜底路径（罕见）。

        先释放目标段锁，再按“段下标升序”同时持有两段锁完成
        “淘汰最大段 LRU 尾部 + 插入目标段”，避免死锁。
        """
        while True:
            victim_idx = max(
                range(self.num_shards), key=lambda i: len(self._shards[i].data)
            )
            lo, hi = sorted((idx, victim_idx))
            with self._shards[lo].lock:
                with self._shards[hi].lock:
                    target = self._shards[idx]
                    if key in target.data:  # 并发下别的线程已插入
                        target.data[key] = value
                        target.data.move_to_end(key)
                        return
                    if self._claim_slot():  # 全局已有空位
                        target.data[key] = value
                        return
                    victim = self._shards[victim_idx]
                    if victim_idx != idx and victim.data:
                        victim.data.popitem(last=False)
                        target.data[key] = value
                        return
                    # 否则状态已变化（如唯一非空段就是目标段），重试

    def delete(self, key: str) -> bool:
        shard = self._shards[self.shard_for_key(key)]
        with shard.lock:
            if key in shard.data:
                del shard.data[key]
                self._release_slot()
                return True
            return False

    def clear(self) -> None:
        for shard in self._shards:  # 逐段清理，不整体停锁
            with shard.lock:
                shard.data.clear()
        with self._size_lock:
            self._size = 0

    # ------------------------------------------------------------------ #
    # 统计与不变量
    # ------------------------------------------------------------------ #
    def stats(self) -> dict:
        """命中率统计：逐段 + 全局汇总（全局视角）。"""
        per_shard = []
        total_hits = total_misses = 0
        for i, shard in enumerate(self._shards):
            with shard.lock:
                hits, misses = shard.hits, shard.misses
                size = len(shard.data)
                quota = shard.quota
            total_hits += hits
            total_misses += misses
            lookups = hits + misses
            per_shard.append(
                {
                    "shard": i,
                    "size": size,
                    "quota": quota,
                    "hits": hits,
                    "misses": misses,
                    "hit_rate": (hits / lookups) if lookups else None,
                }
            )
        total_lookups = total_hits + total_misses
        return {
            "capacity": self.capacity,
            "num_shards": self.num_shards,
            "total_size": sum(s["size"] for s in per_shard),
            "total_hits": total_hits,
            "total_misses": total_misses,
            "global_hit_rate": (total_hits / total_lookups) if total_lookups else None,
            "per_shard": per_shard,
        }

    def assert_capacity_invariant(self) -> None:
        """容量上界断言：各段实际占用之和（=全局计数）不得超过整表容量。"""
        total = 0
        for shard in self._shards:
            with shard.lock:
                total += len(shard.data)
        with self._size_lock:
            counter = self._size
        assert total == counter, f"counter drift: {counter} != actual {total}"
        assert total <= self.capacity, (
            f"global capacity violated: {total} > {self.capacity}"
        )

    def assert_mapping_stable(self, keys, rounds: int = 3) -> None:
        """对本实例的段数做映射稳定性断言。"""
        assert_mapping_stability(keys, self.num_shards, rounds)
