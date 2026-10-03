"""消费者位点记录：确认按位点持久化，并维护"连续水位线"。

存储布局（state_dir 下）：
  acks.log      追加写的确认记录（WAL），每行 {"seq": N}，写后 fsync；
  watermark.json 连续水位线 {"watermark": W}，表示 <= W 的位点全部已确认。

恢复时以 watermark 为起点回放 acks.log，重建已确认集合；
acks.log 超过 compact_after 条时重写压缩。重启后 > watermark 且未确认的
变更会被重新投递（允许少量重复，消费者需幂等）。
"""

from __future__ import annotations

import json
import os
import threading
from typing import Set


class OffsetStore:
    def __init__(self, state_dir: str, compact_after: int = 1000):
        self.state_dir = state_dir
        self.compact_after = compact_after
        self.acks_path = os.path.join(state_dir, "acks.log")
        self.watermark_path = os.path.join(state_dir, "watermark.json")
        self._lock = threading.Lock()
        self._watermark = 0
        self._acked: Set[int] = set()
        self._appends_since_compact = 0
        self._load()

    def _load(self) -> None:
        os.makedirs(self.state_dir, exist_ok=True)
        if os.path.exists(self.watermark_path):
            with open(self.watermark_path, "r", encoding="utf-8") as fh:
                self._watermark = int(json.load(fh)["watermark"])
        if os.path.exists(self.acks_path):
            with open(self.acks_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        seq = int(json.loads(line)["seq"])
                        if seq > self._watermark:
                            self._acked.add(seq)

    def ack(self, seq: int) -> None:
        """确认一个位点：先持久化（fsync），再推进内存状态。"""
        with self._lock:
            if seq <= self._watermark or seq in self._acked:
                return  # 重复确认，幂等忽略
            with open(self.acks_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"seq": seq}) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            self._acked.add(seq)
            self._appends_since_compact += 1
            self._advance_watermark_locked()
            if self._appends_since_compact >= self.compact_after:
                self._compact_locked()

    def _advance_watermark_locked(self) -> None:
        wm = self._watermark
        while wm + 1 in self._acked:
            wm += 1
        if wm > self._watermark:
            self._watermark = wm
            self._acked = {s for s in self._acked if s > wm}
            tmp = self.watermark_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"watermark": wm}))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.watermark_path)

    def _compact_locked(self) -> None:
        tmp = self.acks_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            for seq in sorted(self._acked):
                fh.write(json.dumps({"seq": seq}) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.acks_path)
        self._appends_since_compact = 0

    @property
    def watermark(self) -> int:
        with self._lock:
            return self._watermark

    def is_acked(self, seq: int) -> bool:
        with self._lock:
            return seq <= self._watermark or seq in self._acked

    def acked_ahead(self) -> Set[int]:
        """已确认但高于水位线的位点（用于样例展示/测试）。"""
        with self._lock:
            return set(self._acked)
