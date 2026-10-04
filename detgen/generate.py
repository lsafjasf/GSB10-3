"""确定性数据生成引擎。

设计要点：
- 每行的随机源只由 (种子, 表名, 行号) 派生，与请求的总行数无关，
  因此把行数从 N 调大到 M 时，前 N 行逐字节不变（前缀稳定）。
- 唯一值通过“洗牌取值空间后按下标取”或“确定性碰撞重试”实现，
  洗牌只依赖 (种子, 表, 字段)，同样与行数无关。
- 生成前先做约束可行性检查，无解时抛 ConstraintConflictError。
"""

from __future__ import annotations

from .dataset import Dataset
from .errors import ConstraintConflictError
from .rng import Source, normalize_seed, row_source
from .schema import _TEXT_ALPHABET

# 唯一值空间不超过该阈值时，直接洗牌整个空间（O(空间)）；
# 超过则逐行碰撞重试（O(行数)），避免为超大空间建池。
_SHUFFLE_POOL_LIMIT = 1_000_000


def _shuffled_pool(seed_bytes: bytes, table: str, field: str, values: list) -> list:
    """对取值空间做确定性 Fisher-Yates 洗牌。"""
    source = Source.from_parts(b"detgen-shuffle-v1", seed_bytes, table, field)
    pool = list(values)
    for i in range(len(pool) - 1, 0, -1):
        j = source.rand_index(i + 1)
        pool[i], pool[j] = pool[j], pool[i]
    return pool


def _check_unique_capacity(table_name: str, field, count: int):
    size = field.domain_size()
    if size is not None and size < count:
        raise ConstraintConflictError(
            f"表 {table_name!r} 的 unique 字段 {field.name!r} "
            f"取值空间只有 {size}，无法生成 {count} 个互不相同的值"
        )


def _gen_integer(source: Source, field) -> int:
    return field.min + source.rand_index(field.max - field.min + 1)


def _gen_text(source: Source, length: int) -> str:
    alphabet = _TEXT_ALPHABET
    return "".join(alphabet[source.rand_index(len(alphabet))] for _ in range(length))


def _unique_values(seed_bytes: bytes, table_name: str, field, count: int) -> list:
    """生成 count 个互不相同的值，顺序与 count 无关（前缀稳定）。"""
    size = field.domain_size()
    if size is not None and size <= _SHUFFLE_POOL_LIMIT:
        if field.kind == "integer":
            values = list(range(field.min, field.max + 1))
        elif field.kind == "choice":
            values = list(field.choices)
        else:  # text
            values = None
        if values is not None:
            pool = _shuffled_pool(seed_bytes, table_name, field.name, values)
            return pool[:count]

    # 大空间：逐行生成 + 确定性碰撞重试
    seen = set()
    result = []
    for row_index in range(count):
        source = Source.from_parts(
            b"detgen-unique-v1", seed_bytes, table_name, field.name, row_index
        )
        while True:
            if field.kind == "integer":
                value = _gen_integer(source, field)
            elif field.kind == "text":
                value = _gen_text(source, field.length)
            else:
                value = field.choices[source.rand_index(len(field.choices))]
            if value not in seen:
                seen.add(value)
                result.append(value)
                break
    return result


def generate(schema, counts, seed) -> Dataset:
    """按模式生成数据。

    counts: {表名: 行数}，未列出的表生成 0 行。
    seed:   int / str / bytes，同一种子同一模式必然得到同一结果。
    """
    seed_bytes = normalize_seed(seed)
    tables_data = {}
    for table in schema.tables:
        count = counts.get(table.name, 0)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ConstraintConflictError(
                f"表 {table.name!r} 的行数必须是非负整数，收到 {count!r}"
            )

        # 生成前检查所有 unique 约束在给定行数下是否有解
        for field in table.fields:
            if field.unique:
                _check_unique_capacity(table.name, field, count)

        unique_columns = {}
        for field in table.fields:
            if field.unique:
                unique_columns[field.name] = _unique_values(
                    seed_bytes, table.name, field, count
                )

        rows = []
        for row_index in range(count):
            source = row_source(seed_bytes, table.name, row_index)
            row = {}
            for field in table.fields:
                if field.unique:
                    row[field.name] = unique_columns[field.name][row_index]
                elif field.kind == "integer":
                    row[field.name] = _gen_integer(source, field)
                elif field.kind == "choice":
                    row[field.name] = field.choices[
                        source.rand_index(len(field.choices))
                    ]
                elif field.kind == "boolean":
                    row[field.name] = bool(source.u64() & 1)
                elif field.kind == "text":
                    row[field.name] = _gen_text(source, field.length)
                elif field.kind == "reference":
                    parent_values = tables_data[field.ref_table]["columns"][
                        field.ref_field
                    ]
                    row[field.name] = parent_values[
                        source.rand_index(len(parent_values))
                    ]
            rows.append(row)

        columns = {
            field.name: [row[field.name] for row in rows] for field in table.fields
        }
        tables_data[table.name] = {"rows": rows, "columns": columns}

    return Dataset(
        tables={name: data["rows"] for name, data in tables_data.items()},
        seed=seed_bytes,
    )
