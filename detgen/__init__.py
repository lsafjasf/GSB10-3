"""detgen —— 确定性测试数据生成库（仅标准库）。

同一份代码 + 同一个种子 => 任何进程、任何机器上逐字节一致的数据。
"""

from .dataset import Dataset
from .errors import ConstraintConflictError, DetGenError, SchemaError
from .generate import generate
from .schema import Field, Schema, Table
from .validate import ValidationReport, validate_dataset

__version__ = "1.0.0"

__all__ = [
    "ConstraintConflictError",
    "Dataset",
    "DetGenError",
    "Field",
    "Schema",
    "SchemaError",
    "Table",
    "ValidationReport",
    "generate",
    "validate_dataset",
]
