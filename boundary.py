"""重构后的边界层。

职责：
1. 在进入核心逻辑（第三方模块）之前校验输入，非法输入以可读原因拒绝；
2. 将第三方模块抛出的任意异常隔离并转换为明确的 Result 结果类型；
3. 原子写文件：任何失败都不会在磁盘上留下半成品数据。

只依赖标准库。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from typing import Any, List, Optional

import legacy_third_party as legacy

# ---------------------------------------------------------------------------
# 结果类型
# ---------------------------------------------------------------------------

# 错误码全集：调用方只需处理这三种，无需猜测异常类型。
INVALID_INPUT = "INVALID_INPUT"      # 边界校验拒绝，未进入核心逻辑
INTERNAL_ERROR = "INTERNAL_ERROR"    # 第三方模块内部抛出的异常被隔离转换
IO_ERROR = "IO_ERROR"                # 结果落盘失败


@dataclass(frozen=True)
class ErrorInfo:
    code: str
    message: str


@dataclass(frozen=True)
class Result:
    """明确的返回类型：ok=True 时 value 为报告 dict；ok=False 时 error 必存在。"""

    ok: bool
    value: Optional[dict] = None
    error: Optional[ErrorInfo] = None

    @classmethod
    def success(cls, value: dict) -> "Result":
        return cls(ok=True, value=value)

    @classmethod
    def failure(cls, code: str, message: str) -> "Result":
        return cls(ok=False, error=ErrorInfo(code=code, message=message))


# ---------------------------------------------------------------------------
# 输入校验（边界）
# ---------------------------------------------------------------------------

def _is_number(value: Any) -> bool:
    # bool 是 int 的子类，必须显式排除
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate(record: Any) -> List[str]:
    """返回人类可读的错误原因列表；空列表表示校验通过。"""
    errors: List[str] = []

    if not isinstance(record, dict):
        return [f"record 必须是 JSON 对象，实际为 {type(record).__name__}"]

    # id: 必填，非空字符串
    if "id" not in record:
        errors.append("缺少必填字段 id")
    elif not isinstance(record["id"], str) or not record["id"]:
        errors.append("字段 id 必须是非空字符串")

    # currency: 必填，3 位大写字母（格式校验；具体币种是否受支持由核心逻辑决定）
    if "currency" not in record:
        errors.append("缺少必填字段 currency")
    else:
        cur = record["currency"]
        if (
            not isinstance(cur, str)
            or len(cur) != 3
            or not cur.isalpha()
            or not cur.isupper()
        ):
            errors.append("字段 currency 必须是 3 位大写字母代码（如 CNY、USD）")

    # discount: 可选，[0, 1] 区间内的数值
    if "discount" in record:
        disc = record["discount"]
        if not _is_number(disc):
            errors.append("字段 discount 必须是数值")
        elif not 0.0 <= disc <= 1.0:
            errors.append(f"字段 discount 必须在 [0, 1] 区间内，实际为 {disc}")

    # items: 必填，非空数组
    if "items" not in record:
        errors.append("缺少必填字段 items")
        return errors
    items = record["items"]
    if not isinstance(items, list) or not items:
        errors.append("字段 items 必须是非空数组")
        return errors

    for idx, item in enumerate(items):
        prefix = f"items[{idx}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} 必须是对象，实际为 {type(item).__name__}")
            continue

        if "name" not in item:
            errors.append(f"{prefix} 缺少必填字段 name")
        elif not isinstance(item["name"], str) or not item["name"]:
            errors.append(f"{prefix}.name 必须是非空字符串")

        if "qty" not in item:
            errors.append(f"{prefix} 缺少必填字段 qty")
        elif not _is_number(item["qty"]):
            errors.append(f"{prefix}.qty 必须是数值，实际为 {type(item['qty']).__name__}")
        elif item["qty"] <= 0:
            errors.append(f"{prefix}.qty 必须大于 0，实际为 {item['qty']}")

        if "price" not in item:
            errors.append(f"{prefix} 缺少必填字段 price")
        elif not _is_number(item["price"]):
            errors.append(f"{prefix}.price 必须是数值，实际为 {type(item['price']).__name__}")
        elif item["price"] < 0:
            errors.append(f"{prefix}.price 必须大于等于 0，实际为 {item['price']}")

    return errors


# ---------------------------------------------------------------------------
# 原子写
# ---------------------------------------------------------------------------

def _atomic_write_json(payload: dict, out_path: str) -> None:
    """先写同目录临时文件，fsync 后 os.replace 原子替换，杜绝半成品文件。"""
    directory = os.path.dirname(os.path.abspath(out_path))
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, out_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# 对外入口：校验 -> 隔离执行 -> 原子落盘
# ---------------------------------------------------------------------------

def process(record: Any, out_path: str) -> Result:
    """处理一条账单记录。

    契约：
    - 非法输入：返回 INVALID_INPUT，不进入核心逻辑，不触碰磁盘；
    - 核心逻辑抛异常：返回 INTERNAL_ERROR，异常被隔离，调用方无需 try/except；
    - 落盘失败：返回 IO_ERROR，目标文件要么完整要么不存在（原子写）；
    - 成功：返回 ok 的 Result，value 为报告 dict，且与第三方模块直接计算结果一致。
    """
    errors = validate(record)
    if errors:
        return Result.failure(INVALID_INPUT, "；".join(errors))

    try:
        report = legacy.compute_report(record)
    except Exception as exc:  # 隔离第三方模块的任意异常
        return Result.failure(INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")

    try:
        _atomic_write_json(report, out_path)
    except OSError as exc:
        return Result.failure(IO_ERROR, str(exc))

    return Result.success(report)
