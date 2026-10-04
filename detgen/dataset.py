"""生成结果：行数据 + 规范化序列化 + 摘要。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field as _dc_field


@dataclass(frozen=True)
class Dataset:
    """tables: {表名: [行dict, ...]}，行内字段顺序即声明顺序。"""

    tables: dict
    seed: bytes = _dc_field(default=b"", repr=False)

    def canonical_bytes(self) -> bytes:
        """规范化 JSON 序列化：键排序、无空白、ASCII 转义、UTF-8。"""
        return json.dumps(
            self.tables,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")

    def digest(self) -> str:
        """整份数据的 SHA-256 十六进制摘要。"""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def sliced(self, counts: dict) -> "Dataset":
        """取每个表的前 N 行，用于前缀一致性断言。"""
        return Dataset(
            tables={
                name: rows[: counts.get(name, len(rows))]
                for name, rows in self.tables.items()
            },
            seed=self.seed,
        )
