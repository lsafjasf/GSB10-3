"""变更事件的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass(frozen=True)
class Change:
    """一条数据库变更。

    seq:   全局单调递增位点（LSN），由 ChangeLog 在写入时分配，代表产生顺序。
    key:   主键，同一 key 的变更必须按 seq 升序投递。
    op:    操作类型（insert/update/delete），仅作示例语义。
    value: 变更内容。
    """

    seq: int
    key: str
    op: str
    value: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"seq": self.seq, "key": self.key, "op": self.op, "value": self.value}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Change":
        return cls(
            seq=int(data["seq"]),
            key=str(data["key"]),
            op=str(data["op"]),
            value=dict(data.get("value") or {}),
        )
