"""对已生成的数据集做约束校验，输出结构化报告。"""

from __future__ import annotations

from dataclasses import dataclass, field as _dc_field


@dataclass
class CheckResult:
    name: str
    passed: bool
    violations: list = _dc_field(default_factory=list)


@dataclass
class ValidationReport:
    checks: list = _dc_field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def violation_count(self) -> int:
        return sum(len(check.violations) for check in self.checks)

    def summary_lines(self) -> list:
        lines = []
        for check in self.checks:
            mark = "PASS" if check.passed else "FAIL"
            lines.append(
                f"[{mark}] {check.name}（违规 {len(check.violations)} 处）"
            )
            for violation in check.violations[:5]:
                lines.append(f"       - {violation}")
            if len(check.violations) > 5:
                lines.append(f"       ... 其余 {len(check.violations) - 5} 处省略")
        lines.append(
            f"总体: {'OK' if self.ok else 'FAILED'}，"
            f"共 {self.violation_count} 处违规"
        )
        return lines


def _check(checks, name, violations):
    checks.append(CheckResult(name=name, passed=not violations, violations=violations))


def validate_dataset(schema, dataset) -> ValidationReport:
    """按 schema 声明逐项校验 dataset，返回报告而不抛异常。"""
    checks = []
    tables = dataset.tables

    for table in schema.tables:
        rows = tables.get(table.name, [])
        prefix = f"表 {table.name}"

        # 行结构：字段集合必须与声明一致
        shape_violations = []
        expected = {f.name for f in table.fields}
        for i, row in enumerate(rows):
            if set(row.keys()) != expected:
                shape_violations.append(
                    f"{prefix} 第 {i} 行字段集合 {sorted(row.keys())} "
                    f"!= 声明 {sorted(expected)}"
                )
        _check(checks, f"{prefix} 行结构", shape_violations)

        for field in table.fields:
            name = f"{prefix}.{field.name}"
            values = [row.get(field.name) for row in rows]

            if field.kind == "integer":
                bad = [
                    f"{name} 第 {i} 行取值 {v!r} 越界或类型错误"
                    for i, v in enumerate(values)
                    if not (isinstance(v, int) and not isinstance(v, bool))
                    or not (field.min <= v <= field.max)
                ]
                _check(checks, f"{name} 取值范围 [{field.min}, {field.max}]", bad)
            elif field.kind == "choice":
                allowed = set(field.choices)
                bad = [
                    f"{name} 第 {i} 行取值 {v!r} 不在候选集中"
                    for i, v in enumerate(values)
                    if v not in allowed
                ]
                _check(checks, f"{name} 枚举约束", bad)
            elif field.kind == "boolean":
                bad = [
                    f"{name} 第 {i} 行取值 {v!r} 不是布尔"
                    for i, v in enumerate(values)
                    if not isinstance(v, bool)
                ]
                _check(checks, f"{name} 布尔类型", bad)
            elif field.kind == "text":
                bad = [
                    f"{name} 第 {i} 行取值 {v!r} 不是长度为 {field.length} 的字符串"
                    for i, v in enumerate(values)
                    if not (isinstance(v, str) and len(v) == field.length)
                ]
                _check(checks, f"{name} 文本长度 {field.length}", bad)

            if field.unique:
                seen = set()
                dup = []
                for i, v in enumerate(values):
                    if v in seen:
                        dup.append(f"{name} 第 {i} 行取值 {v!r} 重复")
                    seen.add(v)
                _check(checks, f"{name} 唯一性", dup)

            if field.kind == "reference":
                parent_rows = tables.get(field.ref_table, [])
                parent_values = {
                    row.get(field.ref_field) for row in parent_rows
                }
                bad = [
                    f"{name} 第 {i} 行取值 {v!r} 在被引用表 "
                    f"{field.ref_table}.{field.ref_field} 中不存在"
                    for i, v in enumerate(values)
                    if v not in parent_values
                ]
                _check(checks, f"{name} 引用完整性", bad)

    return ValidationReport(checks=checks)
