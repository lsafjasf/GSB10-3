"""有容量上界的 LRU 瓦片缓存，命中率与淘汰过程可导出为数据。"""

from __future__ import annotations

from collections import OrderedDict


class TileCache:
    """LRU 缓存：访问（get 命中或 put 更新）会把条目移动到最近端；
    容量超限时从最远端淘汰，并在 eviction_log 记录淘汰事件。"""

    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._store: "OrderedDict" = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.eviction_log = []  # [{seq, key, reason, size_after}]
        self._seq = 0

    def __contains__(self, key) -> bool:
        """只查询不命中计数、不改变 LRU 顺序。"""
        return key in self._store

    def get(self, key):
        if key in self._store:
            self.hits += 1
            self._store.move_to_end(key)
            return self._store[key]
        self.misses += 1
        return None

    def put(self, key, value) -> None:
        if key in self._store:
            self._store.move_to_end(key)
            self._store[key] = value
            return
        self._store[key] = value
        while len(self._store) > self.capacity:
            evicted_key, _ = self._store.popitem(last=False)
            self._seq += 1
            self.eviction_log.append({
                "seq": self._seq,
                "key": list(evicted_key) if isinstance(evicted_key, tuple) else evicted_key,
                "reason": "capacity",
                "size_after": len(self._store),
            })

    @property
    def size(self) -> int:
        return len(self._store)

    def keys(self):
        return list(self._store.keys())

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "capacity": self.capacity,
            "size": self.size,
            "hits": self.hits,
            "misses": self.misses,
            "requests": total,
            "hit_rate": (self.hits / total) if total else 0.0,
            "evictions": len(self.eviction_log),
        }
