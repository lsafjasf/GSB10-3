"""防篡改审计日志：哈希链 + Merkle 树承诺，支持任意区间校验。

设计要点：
- 每条记录 i 计算 entry = H(canonical(envelope_i))，
  chain_i = H(chain_{i-1} || entry_i)，与前一条密码学绑定，形成哈希链。
- 所有 chain_i 作为叶子构建 append-only Merkle 树，树根 + 记录条数
  构成 Commitment（可信锚点，可签名/外传保存）。
- 区间校验 [start, end)：只需用包含证明锚定 chain_{start-1}，
  在区间内局部重放哈希链，再锚定 chain_{end-1}。
  代价 O(end-start) + O(log n)，无需重算全链。
- envelope 内携带单调 seq，删除/重排会在缺口处暴露。

仅使用 Python 标准库。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional

GENESIS = b"\x00" * 32  # 链起点（第 -1 条链哈希）


def _hash(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _canon(obj: Any) -> bytes:
    """规范化 JSON 序列化，保证同一记录哈希稳定。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


@dataclass(frozen=True)
class Commitment:
    """某一时刻日志的可信承诺：Merkle 根 + 记录条数。"""
    root: str  # hex
    size: int

    def to_json(self) -> str:
        return json.dumps({"root": self.root, "size": self.size}, sort_keys=True)

    @staticmethod
    def from_json(text: str) -> "Commitment":
        obj = json.loads(text)
        return Commitment(root=obj["root"], size=int(obj["size"]))


@dataclass
class VerifyResult:
    ok: bool
    start: int
    end: int
    reason: str = "ok"               # ok / chain_mismatch / sequence_gap /
                                     # records_missing / boundary_proof_failed /
                                     # range_out_of_commitment
    first_mismatch: Optional[int] = None  # 首个无法验证的记录下标（篡改/缺口定位）
    detail: str = ""

    def __str__(self) -> str:
        if self.ok:
            return f"OK   [{self.start}, {self.end})"
        return (f"FAIL [{self.start}, {self.end}) reason={self.reason} "
                f"at={self.first_mismatch} {self.detail}")


class AuditLog:
    """追加式防篡改审计日志。"""

    def __init__(self) -> None:
        self._envs: list[dict] = []        # {"seq": i, "record": {...}}
        self._chain: list[bytes] = []      # chain_i
        self._levels: list[list[bytes]] = []  # Merkle 树各层（增量维护）

    def __len__(self) -> int:
        return len(self._envs)

    # ------------------------------------------------------------------ 写入

    def append(self, record: dict) -> int:
        """追加一条记录，返回其序号。"""
        seq = len(self._envs)
        env = {"seq": seq, "record": record}
        entry = _hash(_canon(env))
        prev = self._chain[-1] if self._chain else GENESIS
        chain = _hash(prev + entry)
        self._envs.append(env)
        self._chain.append(chain)
        self._tree_append(chain)
        return seq

    def _tree_append(self, node: bytes) -> None:
        """append-only Merkle 树增量追加，奇数节点直接晋升上一层。"""
        if not self._levels:
            self._levels.append([])
        self._levels[0].append(node)
        lvl = 0
        while True:
            cur = self._levels[lvl]
            if len(cur) == 1 and len(self._levels) == lvl + 1:
                return  # 已到达根层
            if len(cur) % 2 == 1:
                promoted = cur[-1]            # 奇数末尾：原样晋升
            else:
                promoted = _hash(cur[-2] + cur[-1])
            if len(self._levels) == lvl + 1:
                self._levels.append([promoted])
                return
            nxt = self._levels[lvl + 1]
            expected = (len(cur) + 1) // 2
            if len(nxt) == expected:
                nxt[-1] = promoted            # 替换之前晋升上来的节点
            else:
                nxt.append(promoted)
            lvl += 1                            # 上层末节点已变，继续向上传播

    # ------------------------------------------------------------------ 承诺

    @property
    def root(self) -> bytes:
        if not self._levels:
            return _hash(b"")
        return self._levels[-1][-1]

    def commitment(self) -> Commitment:
        """导出当前可信锚点（应安全保存/签名，供日后校验）。"""
        return Commitment(root=self.root.hex(), size=len(self._envs))

    # ------------------------------------------------------------------ 证明

    def inclusion_proof(self, index: int) -> list[list[str]]:
        """chain_index 的 Merkle 包含证明，元素为 [hex, 'L'|'R']。"""
        if not 0 <= index < len(self._chain):
            raise IndexError(index)
        proof: list[list[str]] = []
        for level in self._levels[:-1]:
            if index % 2 == 0:
                sib = index + 1
                if sib < len(level):
                    proof.append([level[sib].hex(), "R"])
            else:
                proof.append([level[index - 1].hex(), "L"])
            index //= 2
        return proof

    @staticmethod
    def verify_inclusion(chain_hex: str, proof: list[list[str]],
                         commitment: Commitment) -> bool:
        h = bytes.fromhex(chain_hex)
        for sib_hex, side in proof:
            sib = bytes.fromhex(sib_hex)
            h = _hash(h + sib) if side == "R" else _hash(sib + h)
        return h.hex() == commitment.root

    # ------------------------------------------------------------------ 校验

    def verify_range(self, start: int, end: int,
                     commitment: Optional[Commitment] = None) -> VerifyResult:
        """校验区间 [start, end)，不重算全链。

        代价 O(end-start) 次链重放 + O(log n) 次证明验证。
        失败时 first_mismatch 给出首个被篡改/缺失的记录下标。
        """
        if commitment is None:
            commitment = self.commitment()
        if not (0 <= start <= end):
            raise ValueError(f"非法区间 [{start}, {end})")
        if end > commitment.size:
            return VerifyResult(False, start, end, "range_out_of_commitment",
                                detail=f"区间超出承诺大小 {commitment.size}")
        if end > len(self._envs):
            return VerifyResult(
                False, start, end, "records_missing",
                first_mismatch=len(self._envs),
                detail=f"承诺 {commitment.size} 条，本地仅剩 {len(self._envs)} 条"
                       f"（下标 {len(self._envs)} 起被删除）")

        # 左边界锚定：chain_{start-1} 必须属于可信承诺
        prev = self._chain[start - 1] if start > 0 else GENESIS
        if start > 0 and not self.verify_inclusion(
                prev.hex(), self.inclusion_proof(start - 1), commitment):
            return VerifyResult(False, start, end, "boundary_proof_failed",
                                first_mismatch=start - 1,
                                detail="左边界链哈希不在可信承诺中")

        # 区间内局部重放哈希链
        for k in range(start, end):
            env = self._envs[k]
            if env.get("seq") != k:
                return VerifyResult(
                    False, start, end, "sequence_gap", first_mismatch=k,
                    detail=f"下标 {k} 处期望 seq={k}，实际 seq={env.get('seq')}"
                           f"（记录被删除或重排）")
            chain = _hash(prev + _hash(_canon(env)))
            if chain != self._chain[k]:
                return VerifyResult(False, start, end, "chain_mismatch",
                                    first_mismatch=k,
                                    detail=f"下标 {k} 的记录内容被改动")
            prev = chain

        # 右边界锚定：chain_{end-1} 必须属于可信承诺
        if end > 0 and not self.verify_inclusion(
                self._chain[end - 1].hex(),
                self.inclusion_proof(end - 1), commitment):
            return VerifyResult(False, start, end, "boundary_proof_failed",
                                first_mismatch=end - 1,
                                detail="右边界链哈希不在可信承诺中")
        return VerifyResult(True, start, end)

    def verify_full(self, commitment: Optional[Commitment] = None) -> VerifyResult:
        """全量校验：等价于 verify_range(0, size) + 长度检查。"""
        if commitment is None:
            commitment = self.commitment()
        n = len(self._envs)
        result = self.verify_range(0, n, commitment)
        if not result.ok:
            result.end = commitment.size
            return result
        if n < commitment.size:
            return VerifyResult(
                False, 0, commitment.size, "records_missing", first_mismatch=n,
                detail=f"承诺 {commitment.size} 条，本地仅剩 {n} 条"
                       f"（下标 {n} 起被删除）")
        return VerifyResult(True, 0, n)
