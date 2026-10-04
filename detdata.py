"""detdata — 确定性测试数据生成库（仅标准库）。

核心设计：每条记录的每个字段使用独立的随机流，
流种子 = blake2b(版本 | 主种子 | 实体 | 记录序号 | 字段)。
因此同一条记录的值只取决于 (seed, entity, index, field)，
与实体规模无关 —— 规模变化时已有数据保持逐字节一致（前缀稳定）。

注意：ref 字段的取值是“目标实体第 (随机数 % 目标规模) 行”，
因此目标实体规模变化时，引用行的取值会变化（引用完整性始终成立）。
前缀稳定性的精确含义：某实体的已有行在该实体自身规模增长时逐字节不变。

公开 API：
    Field / Entity / Schema / default_schema  声明式模式定义
    generate(schema, seed, counts)            生成数据（dict）
    canonical_bytes(data)                     规范化字节序列化
    digest(data)                              SHA-256 摘要
    ConflictingConstraintsError               约束冲突异常

CLI：
    python3 detdata.py generate --seed S --users N --orders M
    python3 detdata.py digest   --seed S --users N --orders M
    python3 detdata.py snapshot [--seed S] [--users N] [--orders M] [-o FILE]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field as dc_field
from typing import Dict, List, Optional, Sequence, Tuple

FORMAT_VERSION = "detdata/1"
MASK64 = (1 << 64) - 1
ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
WORD_ALPHABET = "abcdefghijklmnopqrstuvwxyz"


class ConflictingConstraintsError(ValueError):
    """声明的约束互相冲突，任何规模下都无法生成合法数据。"""


class SplitMix64:
    """纯 Python SplitMix64，跨平台、跨 Python 版本逐位一致。"""

    def __init__(self, seed: int):
        self._state = seed & MASK64

    def next_u64(self) -> int:
        self._state = (self._state + 0x9E3779B97F4A7C15) & MASK64
        z = self._state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK64
        return z ^ (z >> 31)

    def randint(self, lo: int, hi: int) -> int:
        return lo + self.next_u64() % (hi - lo + 1)

    def choice(self, seq: Sequence) -> object:
        return seq[self.next_u64() % len(seq)]

    def text(self, length: int, alphabet: str = ALPHABET) -> str:
        return "".join(alphabet[self.next_u64() % len(alphabet)] for _ in range(length))


def _stream_seed(seed: str, entity: str, index: int, field_name: str) -> int:
    h = hashlib.blake2b(digest_size=8)
    h.update(FORMAT_VERSION.encode("utf-8"))
    h.update(b"\x00")
    h.update(str(seed).encode("utf-8"))
    h.update(b"\x00")
    h.update(entity.encode("utf-8"))
    h.update(b"\x00")
    h.update(str(index).encode("ascii"))
    h.update(b"\x00")
    h.update(field_name.encode("utf-8"))
    return int.from_bytes(h.digest(), "big")


@dataclass(frozen=True)
class Field:
    """声明式字段约束。

    kind:
        "int"    整数，约束 min/max/unique
        "string" 定长字符串，约束 length/unique
        "choice" 从 choices 中选取，约束 unique（唯一时要求 count <= len(choices)）
        "ref"    引用其他实体主键，约束 ref_entity/ref_field（引用完整性）
        "email"  唯一邮箱（local-part 内嵌记录序号，天然唯一）
    """

    name: str
    kind: str
    min: Optional[int] = None
    max: Optional[int] = None
    length: Optional[int] = None
    choices: Optional[Tuple[str, ...]] = None
    unique: bool = False
    ref_entity: Optional[str] = None
    ref_field: Optional[str] = None


@dataclass(frozen=True)
class Entity:
    name: str
    fields: Tuple[Field, ...]


@dataclass(frozen=True)
class Schema:
    entities: Tuple[Entity, ...]


_DEFAULT_SCHEMA: Optional[Schema] = None


def default_schema() -> Schema:
    """内置示例模式。code 候选域 300_000，足以支撑超大规模用例。"""
    global _DEFAULT_SCHEMA
    if _DEFAULT_SCHEMA is not None:
        return _DEFAULT_SCHEMA
    _DEFAULT_SCHEMA = Schema(
        entities=(
            Entity(
                "users",
                (
                    Field("id", "int", min=1, max=2**31 - 1, unique=True),
                    Field("code", "choice", choices=tuple(f"C{i:06d}" for i in range(300_000)), unique=True),
                    Field("name", "string", length=8),
                    Field("email", "email"),
                    Field("age", "int", min=18, max=90),
                ),
            ),
            Entity(
                "orders",
                (
                    Field("id", "int", min=1, max=2**31 - 1, unique=True),
                    Field("user_id", "ref", ref_entity="users", ref_field="id"),
                    Field("amount_cents", "int", min=0, max=10_000_000),
                    Field("status", "choice", choices=("new", "paid", "shipped", "done", "cancelled")),
                ),
            ),
        )
    )
    return _DEFAULT_SCHEMA


def _check_conflicts(schema: Schema, counts: Dict[str, int]) -> None:
    """生成前预检：约束冲突时抛出 ConflictingConstraintsError，不产生半截数据。"""
    entity_names = {e.name for e in schema.entities}
    defined: set = set()
    for entity in schema.entities:
        count = counts.get(entity.name, 0)
        if not isinstance(count, int) or count < 0:
            raise ValueError(f"实体 {entity.name!r} 的规模必须是非负整数，得到 {count!r}")
        for f in entity.fields:
            if f.kind == "int":
                if f.min is None or f.max is None:
                    raise ValueError(f"{entity.name}.{f.name}: int 字段必须声明 min/max")
                if f.min > f.max:
                    raise ConflictingConstraintsError(
                        f"{entity.name}.{f.name}: min({f.min}) > max({f.max})，取值范围为空"
                    )
                if f.unique and count > f.max - f.min + 1:
                    raise ConflictingConstraintsError(
                        f"{entity.name}.{f.name}: 需要 {count} 个唯一整数，"
                        f"但 [{f.min}, {f.max}] 只有 {f.max - f.min + 1} 个取值"
                    )
            elif f.kind == "choice":
                if not f.choices:
                    raise ValueError(f"{entity.name}.{f.name}: choice 字段必须声明非空 choices")
                if f.unique and count > len(f.choices):
                    raise ConflictingConstraintsError(
                        f"{entity.name}.{f.name}: 需要 {count} 个唯一取值，"
                        f"但 choices 只有 {len(f.choices)} 个候选"
                    )
            elif f.kind == "ref":
                if not f.ref_entity or not f.ref_field:
                    raise ValueError(f"{entity.name}.{f.name}: ref 字段必须声明 ref_entity/ref_field")
                if f.ref_entity not in entity_names:
                    raise ConflictingConstraintsError(
                        f"{entity.name}.{f.name}: 引用了不存在的实体 {f.ref_entity!r}"
                    )
                if f.ref_entity not in defined:
                    raise ConflictingConstraintsError(
                        f"{entity.name}.{f.name}: 被引用实体 {f.ref_entity!r} 必须先于 {entity.name!r} 定义"
                    )
                if count > 0 and counts.get(f.ref_entity, 0) == 0:
                    raise ConflictingConstraintsError(
                        f"{entity.name}.{f.name}: 需要 {count} 行，但被引用实体 "
                        f"{f.ref_entity!r} 的规模为 0，引用完整性无法满足"
                    )
            elif f.kind in ("string", "email"):
                pass
            else:
                raise ValueError(f"{entity.name}.{f.name}: 未知字段类型 {f.kind!r}")
        defined.add(entity.name)


def _gen_value(rng: SplitMix64, f: Field, index: int, used: set):
    if f.kind == "int":
        if f.unique:
            while True:
                v = rng.randint(f.min, f.max)
                if v not in used:
                    used.add(v)
                    return v
        return rng.randint(f.min, f.max)
    if f.kind == "string":
        return rng.text(f.length or 8)
    if f.kind == "choice":
        if f.unique:
            while True:
                v = rng.choice(f.choices)
                if v not in used:
                    used.add(v)
                    return v
        return rng.choice(f.choices)
    if f.kind == "email":
        return f"u{index}-{rng.text(6)}@example.com"
    raise ValueError(f"未知字段类型 {f.kind!r}")


def generate(schema: Schema, seed: str, counts: Dict[str, int]) -> Dict[str, List[dict]]:
    """按声明式约束生成数据。同一 (schema, seed, counts) 永远产生逐字节相同的结果。"""
    _check_conflicts(schema, counts)
    data: Dict[str, List[dict]] = {}
    for entity in schema.entities:
        count = counts.get(entity.name, 0)
        used: Dict[str, set] = {f.name: set() for f in entity.fields if f.unique}
        rows: List[dict] = []
        for i in range(count):
            row: Dict[str, object] = {}
            for f in entity.fields:
                rng = SplitMix64(_stream_seed(seed, entity.name, i, f.name))
                if f.kind == "ref":
                    target = data[f.ref_entity]
                    row[f.name] = target[rng.next_u64() % len(target)][f.ref_field]
                else:
                    row[f.name] = _gen_value(rng, f, i, used.get(f.name, set()))
            rows.append(row)
        data[entity.name] = rows
    return data


def canonical_bytes(data: Dict[str, List[dict]]) -> bytes:
    """规范化序列化：键排序、紧凑分隔符、UTF-8，保证逐字节可复现。"""
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def digest(data: Dict[str, List[dict]]) -> str:
    return hashlib.sha256(canonical_bytes(data)).hexdigest()


def _cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="detdata", description="确定性测试数据生成")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("generate", "digest", "snapshot", "validate"):
        p = sub.add_parser(name)
        p.add_argument("--seed", default="demo-seed")
        p.add_argument("--users", type=int, default=100)
        p.add_argument("--orders", type=int, default=500)
        if name == "snapshot":
            p.add_argument("-o", "--output", default="snapshot.json")
    args = parser.parse_args(argv)

    counts = {"users": args.users, "orders": args.orders}
    data = generate(default_schema(), args.seed, counts)

    if args.cmd == "generate":
        sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    elif args.cmd == "digest":
        print(digest(data))
    elif args.cmd == "snapshot":
        payload = {
            "format": FORMAT_VERSION,
            "seed": args.seed,
            "counts": counts,
            "sha256": digest(data),
        }
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        print(f"已写入 {args.output}: sha256={payload['sha256']}")
    elif args.cmd == "validate":
        from validator import format_report, validate

        print(format_report(validate(data, default_schema())))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
