"""声明式数据模式定义。

支持的字段类型：
- integer  整数，约束 min <= value <= max
- choice   枚举，取值必须在 choices 中
- boolean  布尔
- text     定长小写字母数字串，约束长度 length
- reference外键，引用另一个表中必须 unique 的目标字段
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import SchemaError

KINDS = ("integer", "choice", "boolean", "text", "reference")
_TEXT_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


@dataclass(frozen=True)
class Field:
    name: str
    kind: str
    min: int | None = None
    max: int | None = None
    choices: tuple | None = None
    length: int | None = None
    ref_table: str | None = None
    ref_field: str | None = None
    unique: bool = False

    @classmethod
    def integer(cls, name, min, max, unique=False):
        return cls(name=name, kind="integer", min=min, max=max, unique=unique)

    @classmethod
    def choice(cls, name, choices, unique=False):
        return cls(
            name=name,
            kind="choice",
            choices=tuple(choices),
            unique=unique,
        )

    @classmethod
    def boolean(cls, name):
        return cls(name=name, kind="boolean")

    @classmethod
    def text(cls, name, length, unique=False):
        return cls(name=name, kind="text", length=length, unique=unique)

    @classmethod
    def reference(cls, name, ref_table, ref_field):
        # 外键字段本身不声明 unique（多个子行可引用同一父行）
        return cls(
            name=name,
            kind="reference",
            ref_table=ref_table,
            ref_field=ref_field,
        )

    def domain_size(self) -> int | None:
        """该字段可能的取值空间大小；不可数（boolean 不去重）时返回 None。"""
        if self.kind == "integer":
            return self.max - self.min + 1
        if self.kind == "choice":
            return len(self.choices)
        if self.kind == "text":
            return len(_TEXT_ALPHABET) ** self.length
        return None


@dataclass(frozen=True)
class Table:
    name: str
    fields: tuple

    def field(self, name) -> Field:
        for field in self.fields:
            if field.name == name:
                return field
        raise KeyError(name)


@dataclass(frozen=True)
class Schema:
    tables: tuple

    @classmethod
    def build(cls, tables) -> "Schema":
        schema = cls(tables=tuple(tables))
        validate_schema(schema)
        return schema

    def table(self, name) -> Table:
        for table in self.tables:
            if table.name == name:
                return table
        raise KeyError(name)


def _validate_field(field: Field):
    if not field.name or not isinstance(field.name, str):
        raise SchemaError("字段名必须是非空字符串")
    if field.kind not in KINDS:
        raise SchemaError(f"字段 {field.name!r} 类型未知: {field.kind!r}")
    if field.kind == "integer":
        if isinstance(field.min, bool) or isinstance(field.max, bool) or not (
            isinstance(field.min, int) and isinstance(field.max, int)
        ):
            raise SchemaError(f"integer 字段 {field.name!r} 的 min/max 必须是整数")
        if field.min > field.max:
            raise SchemaError(
                f"integer 字段 {field.name!r} 的 min({field.min}) > max({field.max})"
            )
    elif field.kind == "choice":
        if not field.choices:
            raise SchemaError(f"choice 字段 {field.name!r} 至少需要一个候选项")
        if len(set(field.choices)) != len(field.choices):
            raise SchemaError(f"choice 字段 {field.name!r} 的候选项有重复")
        for choice in field.choices:
            try:
                hash(choice)
            except TypeError:
                raise SchemaError(
                    f"choice 字段 {field.name!r} 的候选项不可哈希: {choice!r}"
                )
    elif field.kind == "text":
        if not isinstance(field.length, int) or isinstance(field.length, bool):
            raise SchemaError(f"text 字段 {field.name!r} 的 length 必须是整数")
        if field.length <= 0:
            raise SchemaError(f"text 字段 {field.name!r} 的 length 必须为正数")
    elif field.kind == "reference":
        if not field.ref_table or not field.ref_field:
            raise SchemaError(f"reference 字段 {field.name!r} 缺少引用目标")
        if field.unique:
            raise SchemaError(f"reference 字段 {field.name!r} 不允许声明 unique")


def validate_schema(schema: Schema):
    """静态校验模式声明，非法时抛 SchemaError。"""
    seen_tables = set()
    for table in schema.tables:
        if not table.name or not isinstance(table.name, str):
            raise SchemaError("表名必须是非空字符串")
        if table.name in seen_tables:
            raise SchemaError(f"表名重复: {table.name!r}")
        seen_tables.add(table.name)
        if not table.fields:
            raise SchemaError(f"表 {table.name!r} 至少需要一个字段")
        seen_fields = set()
        for field in table.fields:
            _validate_field(field)
            if field.name in seen_fields:
                raise SchemaError(
                    f"表 {table.name!r} 字段名重复: {field.name!r}"
                )
            seen_fields.add(field.name)

    by_name = {table.name: table for table in schema.tables}
    for table in schema.tables:
        for field in table.fields:
            if field.kind != "reference":
                continue
            target = by_name.get(field.ref_table)
            if target is None:
                raise SchemaError(
                    f"表 {table.name!r}.{field.name} 引用了不存在的表 "
                    f"{field.ref_table!r}"
                )
            try:
                target_field = target.field(field.ref_field)
            except KeyError:
                raise SchemaError(
                    f"表 {table.name!r}.{field.name} 引用了不存在的字段 "
                    f"{field.ref_table!r}.{field.ref_field!r}"
                )
            if target_field.kind == "reference":
                raise SchemaError(
                    f"表 {table.name!r}.{field.name} 不能引用外键字段 "
                    f"{field.ref_table!r}.{field.ref_field!r}"
                )
            if not target_field.unique:
                raise SchemaError(
                    f"被引用字段 {field.ref_table!r}.{field.ref_field!r} "
                    f"必须声明 unique 才能保证引用完整性"
                )
            # 被引用表必须先声明，保证生成顺序
            target_index = schema.tables.index(target)
            if target_index > schema.tables.index(table):
                raise SchemaError(
                    f"表 {table.name!r} 引用的 {field.ref_table!r} 必须在其之前声明"
                )
