"""变更日志：追加写、按位点读取。每条变更在写入时获得全局单调位点 seq。"""

from __future__ import annotations

import json
import os
import threading
from typing import List, Optional

from .model import Change


class ChangeLog:
    """只追加的变更日志（JSONL 文件），写入即 fsync，保证重启不丢。"""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._next_seq = 1
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        self._next_seq = max(self._next_seq, int(json.loads(line)["seq"]) + 1)

    def append(self, key: str, op: str, value: dict) -> Change:
        """追加一条变更并分配位点，返回带 seq 的 Change。"""
        with self._lock:
            change = Change(seq=self._next_seq, key=key, op=op, value=dict(value))
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(change.to_dict(), ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            self._next_seq += 1
            return change

    def read_from(self, after_seq: int = 0, limit: Optional[int] = None) -> List[Change]:
        """读取 seq > after_seq 的变更，按 seq 升序。"""
        out: List[Change] = []
        if not os.path.exists(self.path):
            return out
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                change = Change.from_dict(json.loads(line))
                if change.seq > after_seq:
                    out.append(change)
                    if limit is not None and len(out) >= limit:
                        break
        return out
