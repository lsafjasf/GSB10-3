"""detgen 的异常类型。"""


class DetGenError(Exception):
    """库内所有错误的基类。"""


class SchemaError(DetGenError):
    """模式（schema）声明本身非法，例如字段类型未知、引用不存在的表。"""


class ConstraintConflictError(DetGenError):
    """声明的约束互相冲突，在给定行数下无解，例如唯一值空间不足。"""
