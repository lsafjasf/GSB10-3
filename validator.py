"""validator — 对 detdata 生成的数据做声明式约束校验。

校验规则直接来自 Schema 中的 Field 声明：
    int    -> 取值范围 [min, max]，可选唯一性
    string -> 长度、字符集，可选唯一性
    choice -> 取值必须属于 choices，可选唯一性
    ref    -> 引用完整性（值必须存在于目标实体的目标字段中）
    email  -> 格式与唯一性

validate(data, schema) 返回结构化报告 dict；format_report 输出人类可读文本。
按列校验 + 惰性构造违规消息，百万级行数也可在数秒内完成。
"""

from __future__ import annotations

import re
from typing import Dict, List

from detdata import ALPHABET, Field, Schema

MAX_SAMPLES = 10
EMAIL_RE = re.compile(r"^[a-z0-9-]+@[a-z0-9.-]+\.[a-z]{2,}$")
_STRING_RE_CACHE: Dict[int, "re.Pattern"] = {}


def _string_re(length: int) -> "re.Pattern":
    if length not in _STRING_RE_CACHE:
        _STRING_RE_CACHE[length] = re.compile(
            "[%s]{%d}" % (re.escape(ALPHABET), length)
        )
    return _STRING_RE_CACHE[length]


def _check_column(values: list, f: Field, entity_name: str,
                  key_sets: Dict[str, Dict[str, set]]) -> dict:
    """校验单个字段的整列取值，返回 {"violations": n, "samples": [...]}。"""
    violations = 0
    samples: List[str] = []

    def fail(idx: int, message: str) -> None:
        nonlocal violations
        violations += 1
        if len(samples) < MAX_SAMPLES:
            samples.append(f"{entity_name}[{idx}].{f.name}: {message}")

    if f.kind == "int":
        lo, hi = f.min, f.max
        for idx, v in enumerate(values):
            if not isinstance(v, int) or not (lo <= v <= hi):
                fail(idx, f"{v!r} 超出 [{lo}, {hi}]")
    elif f.kind == "string":
        pattern = _string_re(f.length)
        for idx, v in enumerate(values):
            if not isinstance(v, str) or not pattern.fullmatch(v):
                fail(idx, f"{v!r} 长度/字符集不符")
    elif f.kind == "choice":
        allowed = set(f.choices)
        for idx, v in enumerate(values):
            if v not in allowed:
                fail(idx, f"{v!r} 不在 choices 中")
    elif f.kind == "email":
        for idx, v in enumerate(values):
            if not isinstance(v, str) or not EMAIL_RE.match(v):
                fail(idx, f"{v!r} 邮箱格式非法")
    elif f.kind == "ref":
        target = key_sets[f.ref_entity][f.ref_field]
        for idx, v in enumerate(values):
            if v not in target:
                fail(idx, f"{v!r} 在 {f.ref_entity}.{f.ref_field} 中不存在")

    if f.unique:
        distinct = len(set(values))
        if distinct != len(values):
            dup_count = len(values) - distinct
            seen = set()
            dup_samples = []
            for idx, v in enumerate(values):
                if v in seen and len(dup_samples) < MAX_SAMPLES:
                    dup_samples.append(f"{entity_name}[{idx}].{f.name}: {v!r} 重复")
                seen.add(v)
            violations += dup_count
            samples.extend(dup_samples)

    return {"violations": violations, "samples": samples}


def validate(data: Dict[str, List[dict]], schema: Schema) -> dict:
    """返回 {"ok": bool, "entities": {name: {...}}}，ok 为 True 表示全部约束通过。"""
    key_sets: Dict[str, Dict[str, set]] = {}
    for entity in schema.entities:
        rows = data.get(entity.name, [])
        key_sets[entity.name] = {
            f.name: {row[f.name] for row in rows} for f in entity.fields
        }

    report = {"ok": True, "entities": {}}
    for entity in schema.entities:
        rows = data.get(entity.name, [])
        ent = {"count": len(rows), "fields": {}}
        for f in entity.fields:
            values = [row[f.name] for row in rows]
            result = _check_column(values, f, entity.name, key_sets)
            if result["violations"]:
                ent["fields"][f.name] = result
        ent["ok"] = not ent["fields"]
        report["entities"][entity.name] = ent
        report["ok"] = report["ok"] and ent["ok"]
    return report


def format_report(report: dict) -> str:
    lines = []
    for name, ent in report["entities"].items():
        status = "OK" if ent["ok"] else "FAIL"
        lines.append(f"[{status}] {name}: {ent['count']} 行")
        for field_name, rule in ent["fields"].items():
            lines.append(f"    {field_name}: {rule['violations']} 处违规")
            for sample in rule["samples"]:
                lines.append(f"      - {sample}")
    lines.append("总体: " + ("全部约束通过" if report["ok"] else "存在约束违规"))
    return "\n".join(lines)
