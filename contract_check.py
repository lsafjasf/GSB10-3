"""契约双向校验库（仅标准库）。

校验服务提供方(provider)与消费方(consumer)各自维护的接口定义是否互相兼容：

1. 正向：提供方是否满足消费方声明的全部必需字段与类型（逐字段结果）。
2. 反向：消费方是否容忍提供方新增的字段；提供方缺的字段必须报出来。

Schema 格式（JSON）::

    {
      "name": "order-api",
      "additional_fields": "tolerate" | "strict",   # 对未知字段的策略，默认 tolerate
      "fields": [
        {"name": "id",     "type": "integer", "required": true},
        {"name": "amount", "type": "number",  "required": true},
        {"name": "note",   "type": "string",  "required": false, "nullable": true},
        {"name": "tags",   "type": "array",   "required": false,
         "items": {"type": "string"}},
        {"name": "user",   "type": "object",  "required": true,
         "fields": [ ... 嵌套字段 ... ]}
      ]
    }

支持的类型：string / integer / number / boolean / object / array / any
类型兼容规则：完全相同；或消费方是 number 而提供方是 integer（整数是数的子集）；
或消费方是 any（接受一切）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Severity(str, Enum):
    """严重程度分级。"""

    ERROR = "ERROR"      # 破坏性变更：契约不兼容，必须处理
    WARNING = "WARNING"  # 非破坏但需知晓：如提供方新增了消费方不认识的字段
    INFO = "INFO"        # 提示性信息：如消费方的可选字段在提供方不存在
    OK = "OK"            # 该字段完全兼容


class Direction(str, Enum):
    FORWARD = "provider->consumer"   # 提供方是否满足消费方
    REVERSE = "consumer->provider"   # 消费方是否容忍提供方


@dataclass
class FieldResult:
    """单个字段的校验结果。"""

    path: str                 # 字段路径，如 user.address.city
    direction: Direction
    severity: Severity
    code: str                 # 机器可读的结果码
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "direction": self.direction.value,
            "severity": self.severity.value,
            "code": self.code,
            "message": self.message,
        }


@dataclass
class CheckReport:
    """一次完整双向校验的报告。"""

    results: List[FieldResult] = field(default_factory=list)

    @property
    def compatible(self) -> bool:
        """不存在 ERROR 级问题即视为兼容（WARNING 不阻断）。"""
        return not any(r.severity == Severity.ERROR for r in self.results)

    def incompatibilities(self) -> List[FieldResult]:
        """不兼容清单：ERROR 在前，WARNING 在后。"""
        order = {Severity.ERROR: 0, Severity.WARNING: 1}
        return sorted(
            (r for r in self.results if r.severity in order),
            key=lambda r: (order[r.severity], r.path),
        )

    def by_severity(self, severity: Severity) -> List[FieldResult]:
        return [r for r in self.results if r.severity == severity]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "compatible": self.compatible,
            "summary": {
                s.value: len(self.by_severity(s))
                for s in (Severity.ERROR, Severity.WARNING, Severity.INFO, Severity.OK)
            },
            "incompatibilities": [r.to_dict() for r in self.incompatibilities()],
            "field_results": [r.to_dict() for r in self.results],
        }


# ---------------------------------------------------------------------------
# Schema 解析
# ---------------------------------------------------------------------------

KNOWN_TYPES = {"string", "integer", "number", "boolean", "object", "array", "any"}


class SchemaError(ValueError):
    """接口定义本身不合法。"""


@dataclass
class FieldDef:
    name: str
    type: str = "any"
    required: bool = False
    nullable: bool = False
    fields: List["FieldDef"] = field(default_factory=list)   # object 的子字段
    items: Optional["FieldDef"] = None                       # array 的元素定义


@dataclass
class Schema:
    name: str = ""
    additional_fields: str = "tolerate"   # tolerate | strict
    fields: List[FieldDef] = field(default_factory=list)

    def field_map(self) -> Dict[str, FieldDef]:
        return {f.name: f for f in self.fields}


def _parse_field(raw: Dict[str, Any], path: str) -> FieldDef:
    if not isinstance(raw, dict) or "name" not in raw:
        raise SchemaError(f"字段定义缺少 name: {path or '<root>'}")
    name = raw["name"]
    ftype = raw.get("type", "any")
    if ftype not in KNOWN_TYPES:
        raise SchemaError(f"字段 {path + name} 类型未知: {ftype!r}")
    full = f"{path}{name}"
    children: List[FieldDef] = []
    if ftype == "object":
        children = [_parse_field(c, full + ".") for c in raw.get("fields", [])]
    items = None
    if ftype == "array" and "items" in raw:
        item_raw = dict(raw["items"])
        item_raw.setdefault("name", f"{name}[]")
        items = _parse_field(item_raw, path)
    return FieldDef(
        name=name,
        type=ftype,
        required=bool(raw.get("required", False)),
        nullable=bool(raw.get("nullable", False)),
        fields=children,
        items=items,
    )


def load_schema(raw: Dict[str, Any]) -> Schema:
    if not isinstance(raw, dict):
        raise SchemaError("schema 必须是 JSON 对象")
    policy = raw.get("additional_fields", "tolerate")
    if policy not in ("tolerate", "strict"):
        raise SchemaError(f"additional_fields 只能是 tolerate/strict，得到 {policy!r}")
    return Schema(
        name=raw.get("name", ""),
        additional_fields=policy,
        fields=[_parse_field(f, "") for f in raw.get("fields", [])],
    )


def load_schema_file(path: str) -> Schema:
    with open(path, "r", encoding="utf-8") as fh:
        return load_schema(json.load(fh))


# ---------------------------------------------------------------------------
# 类型兼容
# ---------------------------------------------------------------------------

def types_compatible(consumer_type: str, provider_type: str) -> bool:
    """提供方类型是否能满足消费方期望的类型。"""
    if consumer_type == "any" or consumer_type == provider_type:
        return True
    # integer 是 number 的子集：消费方要 number，提供方给 integer 是安全的
    if consumer_type == "number" and provider_type == "integer":
        return True
    return False


# ---------------------------------------------------------------------------
# 正向校验：提供方是否满足消费方
# ---------------------------------------------------------------------------

def _check_forward(consumer_fields: List[FieldDef], provider_map: Dict[str, FieldDef],
                   path: str, report: CheckReport) -> None:
    for c_field in consumer_fields:
        full = path + c_field.name
        p_field = provider_map.get(c_field.name)

        if p_field is None:
            if c_field.required:
                report.results.append(FieldResult(
                    path=full, direction=Direction.FORWARD,
                    severity=Severity.ERROR, code="REQUIRED_FIELD_MISSING",
                    message=f"消费方必需字段 {full} 在提供方不存在",
                ))
            else:
                report.results.append(FieldResult(
                    path=full, direction=Direction.FORWARD,
                    severity=Severity.INFO, code="OPTIONAL_FIELD_ABSENT",
                    message=f"消费方可选字段 {full} 在提供方不存在（可容忍）",
                ))
            continue

        if not types_compatible(c_field.type, p_field.type):
            report.results.append(FieldResult(
                path=full, direction=Direction.FORWARD,
                severity=Severity.ERROR, code="TYPE_MISMATCH",
                message=f"字段 {full} 类型不兼容：消费方期望 {c_field.type}，"
                        f"提供方给出 {p_field.type}",
            ))
            continue

        # 提供方把消费方的必需字段标成了可选：运行期可能拿不到值
        if c_field.required and not p_field.required:
            report.results.append(FieldResult(
                path=full, direction=Direction.FORWARD,
                severity=Severity.WARNING, code="REQUIRED_BECAME_OPTIONAL",
                message=f"字段 {full} 在消费方是必需的，但提供方标记为可选，"
                        f"运行期可能缺失",
            ))
        else:
            report.results.append(FieldResult(
                path=full, direction=Direction.FORWARD,
                severity=Severity.OK, code="FIELD_OK",
                message=f"字段 {full} 兼容（{c_field.type}）",
            ))

        if c_field.type == "object":
            _check_forward(c_field.fields, {f.name: f for f in p_field.fields},
                           full + ".", report)
        elif c_field.type == "array" and c_field.items and p_field.items:
            _check_forward([c_field.items], {p_field.items.name: p_field.items},
                           path, report)


# ---------------------------------------------------------------------------
# 反向校验：消费方是否容忍提供方
# ---------------------------------------------------------------------------

def _check_reverse(provider_fields: List[FieldDef], consumer_map: Dict[str, FieldDef],
                   path: str, policy: str, report: CheckReport) -> None:
    for p_field in provider_fields:
        full = path + p_field.name
        c_field = consumer_map.get(p_field.name)

        if c_field is None:
            # 提供方新增字段：按消费方对未知字段的策略定级，绝不静默忽略
            if policy == "strict":
                report.results.append(FieldResult(
                    path=full, direction=Direction.REVERSE,
                    severity=Severity.ERROR, code="UNKNOWN_FIELD_REJECTED",
                    message=f"提供方字段 {full} 不在消费方契约中，"
                            f"且消费方策略为 strict（拒绝未知字段）",
                ))
            else:
                report.results.append(FieldResult(
                    path=full, direction=Direction.REVERSE,
                    severity=Severity.WARNING, code="UNKNOWN_FIELD_TOLERATED",
                    message=f"提供方新增字段 {full} 不在消费方契约中"
                            f"（策略 tolerate，建议消费方知晓）",
                ))
            continue

        # 两侧都存在但类型不兼容：反向同样要报（正向已报过类型，这里标注视角）
        if not types_compatible(c_field.type, p_field.type):
            report.results.append(FieldResult(
                path=full, direction=Direction.REVERSE,
                severity=Severity.ERROR, code="TYPE_MISMATCH",
                message=f"字段 {full} 类型不兼容：提供方给出 {p_field.type}，"
                        f"消费方只能处理 {c_field.type}",
            ))
            continue

        # 提供方必需、消费方却标可选：消费方可能丢弃该数据
        if p_field.required and not c_field.required:
            report.results.append(FieldResult(
                path=full, direction=Direction.REVERSE,
                severity=Severity.WARNING, code="PROVIDER_REQUIRED_CONSUMER_OPTIONAL",
                message=f"提供方必需字段 {full} 在消费方是可选的，"
                        f"消费方可能未消费该数据",
            ))

        if p_field.type == "object":
            _check_reverse(p_field.fields, {f.name: f for f in c_field.fields},
                           full + ".", policy, report)
        elif p_field.type == "array" and p_field.items and c_field.items:
            _check_reverse([p_field.items], {c_field.items.name: c_field.items},
                           path, policy, report)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def check_contract(consumer: Schema, provider: Schema) -> CheckReport:
    """对消费方与提供方契约做双向校验，返回完整报告。"""
    report = CheckReport()
    _check_forward(consumer.fields, provider.field_map(), "", report)
    _check_reverse(provider.fields, consumer.field_map(), "",
                   consumer.additional_fields, report)
    return report


def check_contract_files(consumer_path: str, provider_path: str) -> CheckReport:
    return check_contract(load_schema_file(consumer_path),
                          load_schema_file(provider_path))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _render(report: CheckReport) -> str:
    lines: List[str] = []
    lines.append("== 逐字段检查结果 ==")
    for r in report.results:
        lines.append(f"[{r.severity.value:7}] ({r.direction.value}) "
                     f"{r.path}: {r.code} - {r.message}")
    lines.append("")
    lines.append("== 不兼容清单（按严重程度） ==")
    bad = report.incompatibilities()
    if not bad:
        lines.append("（无）")
    for r in bad:
        lines.append(f"[{r.severity.value:7}] {r.path}: {r.message}")
    lines.append("")
    lines.append(f"总体结论: {'兼容' if report.compatible else '不兼容'}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="契约双向校验")
    parser.add_argument("consumer", help="消费方契约 JSON")
    parser.add_argument("provider", help="提供方契约 JSON")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出报告")
    args = parser.parse_args(argv)

    report = check_contract_files(args.consumer, args.provider)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_render(report))
    return 0 if report.compatible else 1


if __name__ == "__main__":
    raise SystemExit(main())
