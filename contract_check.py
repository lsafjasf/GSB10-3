"""契约双向校验库（仅标准库）。

服务提供方（provider）与消费方（consumer）各自维护接口定义（JSON 契约），
本库对两份契约做双向兼容性校验：

正向（provider -> consumer）：
    提供方是否满足消费方声明的全部必需字段与类型，逐字段给出结果。
反向（consumer -> provider）：
    消费方是否能容忍提供方新增的字段；提供方缺失消费方期望的字段必须报出，
    而不是静默忽略。

契约格式（JSON）::

    {
      "name": "order-api",
      "version": "1.2.0",
      "strict": false,                 // 仅消费方有意义：是否拒绝未知字段
      "fields": {
        "id":     {"type": "integer", "required": true},
        "amount": {"type": "number",  "required": true},
        "tags":   {"type": "array",   "required": false, "items": "string"},
        "addr":   {"type": "object",  "required": false,
                   "fields": {"city": {"type": "string", "required": true}}}
      }
    }

支持的类型：string / integer / number / boolean / array / object / any。
integer 可兼容 number（消费方要 number，提供方给 integer 视为兼容）。

严重程度：
    ERROR   不兼容变更（破坏调用）：必需字段缺失、类型不匹配、严格模式下出现未知字段
    WARNING 有风险但通常可运行：可选字段被删除、出现未声明的新增字段
    INFO    提示性信息
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Severity(Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class Status(Enum):
    OK = "OK"
    MISSING_FIELD = "MISSING_FIELD"
    TYPE_MISMATCH = "TYPE_MISMATCH"
    UNEXPECTED_FIELD = "UNEXPECTED_FIELD"
    OPTIONAL_REMOVED = "OPTIONAL_REMOVED"


_VALID_TYPES = {"string", "integer", "number", "boolean", "array", "object", "any"}

# 消费方期望类型 -> 可接受的提供方类型集合（子类型兼容）
_TYPE_COMPAT = {
    "any": _VALID_TYPES,
    "number": {"integer", "number"},
}


@dataclass
class FieldResult:
    """单个字段的检查结果。"""

    path: str                 # 字段路径，如 "addr.city"
    direction: str            # "forward"（提供方->消费方）或 "reverse"（消费方->提供方）
    status: Status
    severity: Severity
    message: str
    expected: Optional[str] = None   # 期望类型
    actual: Optional[str] = None     # 实际类型

    def __str__(self) -> str:
        detail = f"[{self.severity.value}] {self.path}: {self.message}"
        if self.expected or self.actual:
            detail += f" (expected={self.expected}, actual={self.actual})"
        return detail


@dataclass
class CheckReport:
    """双向校验报告。"""

    provider_name: str
    consumer_name: str
    results: List[FieldResult] = field(default_factory=list)

    @property
    def compatible(self) -> bool:
        """无 ERROR 级问题即视为可兼容调用。"""
        return not any(r.severity is Severity.ERROR for r in self.results)

    def by_severity(self, severity: Severity) -> List[FieldResult]:
        return [r for r in self.results if r.severity is severity]

    def incompatible_fields(self) -> List[FieldResult]:
        """不兼容（ERROR 级）字段清单。"""
        return self.by_severity(Severity.ERROR)

    def render(self) -> str:
        lines = [
            f"契约双向校验: provider={self.provider_name!r} consumer={self.consumer_name!r}",
            "",
            "== 逐字段检查结果 ==",
        ]
        if not self.results:
            lines.append("  (无字段可检查)")
        for r in self.results:
            lines.append(f"  [{r.direction:7s}] {r.status.value:16s} {r}")
        lines.append("")
        lines.append("== 不兼容清单（按严重程度分级） ==")
        for sev in (Severity.ERROR, Severity.WARNING, Severity.INFO):
            items = self.by_severity(sev)
            lines.append(f"  {sev.value} ({len(items)}):")
            for r in items:
                lines.append(f"    - {r.path}: {r.message}")
        lines.append("")
        lines.append(f"结论: {'兼容' if self.compatible else '不兼容'}")
        return "\n".join(lines)


def _type_of(spec: Dict[str, Any]) -> str:
    return spec.get("type", "any")


def _types_compatible(expected: str, actual: str) -> bool:
    if expected == actual:
        return True
    return actual in _TYPE_COMPAT.get(expected, set())


def _check_forward(provider_fields: Dict[str, Dict[str, Any]],
                   consumer_fields: Dict[str, Dict[str, Any]],
                   path: str, results: List[FieldResult]) -> None:
    """正向：提供方是否满足消费方声明的字段与类型。"""
    for name, c_spec in consumer_fields.items():
        field_path = f"{path}{name}"
        c_type = _type_of(c_spec)
        required = bool(c_spec.get("required", False))
        p_spec = provider_fields.get(name)

        if p_spec is None:
            if required:
                results.append(FieldResult(
                    path=field_path, direction="forward",
                    status=Status.MISSING_FIELD, severity=Severity.ERROR,
                    message="消费方必需字段在提供方契约中缺失",
                    expected=c_type, actual=None))
            else:
                results.append(FieldResult(
                    path=field_path, direction="forward",
                    status=Status.OPTIONAL_REMOVED, severity=Severity.WARNING,
                    message="消费方可选字段在提供方契约中不存在（可能已被删除）",
                    expected=c_type, actual=None))
            continue

        p_type = _type_of(p_spec)
        if not _types_compatible(c_type, p_type):
            results.append(FieldResult(
                path=field_path, direction="forward",
                status=Status.TYPE_MISMATCH, severity=Severity.ERROR,
                message="字段类型不兼容",
                expected=c_type, actual=p_type))
            continue

        results.append(FieldResult(
            path=field_path, direction="forward",
            status=Status.OK, severity=Severity.INFO,
            message="字段存在且类型兼容",
            expected=c_type, actual=p_type))

        if c_type == "object" and p_type == "object":
            _check_forward(p_spec.get("fields", {}), c_spec.get("fields", {}),
                           field_path + ".", results)
        elif c_type == "array" and p_type == "array":
            _check_array_items(p_spec, c_spec, field_path, "forward", results)


def _check_reverse(provider_fields: Dict[str, Dict[str, Any]],
                   consumer_fields: Dict[str, Dict[str, Any]],
                   path: str, results: List[FieldResult],
                   consumer_strict: bool) -> None:
    """反向：消费方是否容忍提供方新增的字段。缺字段必须报出。"""
    for name, p_spec in provider_fields.items():
        field_path = f"{path}{name}"
        p_type = _type_of(p_spec)
        c_spec = consumer_fields.get(name)

        if c_spec is None:
            if consumer_strict:
                severity = Severity.ERROR
                note = "消费方处于 strict 模式，未知字段将被拒绝"
            else:
                severity = Severity.WARNING
                note = "提供方新增字段，消费方未声明（需确认消费方可安全忽略）"
            results.append(FieldResult(
                path=field_path, direction="reverse",
                status=Status.UNEXPECTED_FIELD, severity=severity,
                message=note, expected=None, actual=p_type))
            continue

        c_type = _type_of(c_spec)
        if p_type == "object" and c_type == "object":
            _check_reverse(p_spec.get("fields", {}), c_spec.get("fields", {}),
                           field_path + ".", results, consumer_strict)


def _check_array_items(p_spec: Dict[str, Any], c_spec: Dict[str, Any],
                       path: str, direction: str,
                       results: List[FieldResult]) -> None:
    p_item = p_spec.get("items", "any")
    c_item = c_spec.get("items", "any")
    if not _types_compatible(c_item, p_item):
        results.append(FieldResult(
            path=path + "[]", direction=direction,
            status=Status.TYPE_MISMATCH, severity=Severity.ERROR,
            message="数组元素类型不兼容", expected=c_item, actual=p_item))


def validate_contract(contract: Dict[str, Any], label: str = "contract") -> List[str]:
    """校验契约文件本身的结构合法性，返回错误列表（空列表表示合法）。"""
    errors: List[str] = []
    if not isinstance(contract, dict):
        return [f"{label}: 契约必须是 JSON 对象"]
    fields = contract.get("fields")
    if not isinstance(fields, dict):
        errors.append(f"{label}: 缺少 'fields' 对象")
        return errors

    def walk(field_map: Dict[str, Any], prefix: str) -> None:
        for name, spec in field_map.items():
            fp = f"{prefix}{name}"
            if not isinstance(spec, dict):
                errors.append(f"{label}: 字段 {fp} 的定义必须是对象")
                continue
            t = spec.get("type")
            if t is None:
                errors.append(f"{label}: 字段 {fp} 缺少 'type'")
            elif t not in _VALID_TYPES:
                errors.append(f"{label}: 字段 {fp} 类型非法: {t!r}")
            if t == "object" and "fields" in spec:
                if not isinstance(spec["fields"], dict):
                    errors.append(f"{label}: 字段 {fp}.fields 必须是对象")
                else:
                    walk(spec["fields"], fp + ".")

    walk(fields, "")
    return errors


def check_compatibility(provider: Dict[str, Any],
                        consumer: Dict[str, Any]) -> CheckReport:
    """对提供方与消费方契约做双向校验，返回报告。"""
    report = CheckReport(
        provider_name=provider.get("name", "<provider>"),
        consumer_name=consumer.get("name", "<consumer>"),
    )
    p_fields = provider.get("fields", {})
    c_fields = consumer.get("fields", {})
    consumer_strict = bool(consumer.get("strict", False))

    _check_forward(p_fields, c_fields, "", report.results)
    _check_reverse(p_fields, c_fields, "", report.results, consumer_strict)
    return report


def load_contract(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="契约双向校验：检查提供方与消费方接口定义是否互相兼容")
    parser.add_argument("provider", help="提供方契约 JSON 文件")
    parser.add_argument("consumer", help="消费方契约 JSON 文件")
    args = parser.parse_args(argv)

    provider = load_contract(args.provider)
    consumer = load_contract(args.consumer)

    errors = validate_contract(provider, "provider") + \
        validate_contract(consumer, "consumer")
    if errors:
        for err in errors:
            print(f"契约结构错误: {err}", file=sys.stderr)
        return 2

    report = check_compatibility(provider, consumer)
    print(report.render())
    return 0 if report.compatible else 1


if __name__ == "__main__":
    sys.exit(main())
