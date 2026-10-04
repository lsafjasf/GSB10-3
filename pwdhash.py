"""口令哈希与参数升级库（仅标准库）。

记录格式（自描述、可直接入库为字符串）::

    pbkdf2_sha256$260000$<base64 盐>$<base64 哈希>

- 算法标识与参数（迭代次数、盐、派生长度）全部保存在每条记录里；
- 校验时严格按“记录中”的参数重算，不读取任何全局参数；
- 记录参数落后于当前策略时，在“登录成功后”用新参数（含新随机盐）重哈希，
  以新记录就地替换旧记录，完成平滑升级。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import NamedTuple, Optional

# ---- 参数策略（随硬件演进而调整；旧记录仍按其内嵌参数可校验） -------------

# 算法标识 -> hashlib 可用的 HMAC 摘要名
_SUPPORTED_ALGORITHMS = {
    "pbkdf2_sha256": "sha256",
    "pbkdf2_sha512": "sha512",
}

# 防止恶意/损坏记录写入超大迭代次数造成 DoS
MAX_ITERATIONS = 10_000_000


class Params(NamedTuple):
    """生成新记录时使用的哈希参数。"""

    algorithm: str = "pbkdf2_sha256"
    iterations: int = 260_000
    salt_bytes: int = 16
    dklen: int = 32

    def validate(self) -> None:
        if self.algorithm not in _SUPPORTED_ALGORITHMS:
            raise ValueError(f"unsupported algorithm: {self.algorithm!r}")
        if not isinstance(self.iterations, int) or isinstance(self.iterations, bool):
            raise ValueError("iterations must be int")
        if not (0 < self.iterations <= MAX_ITERATIONS):
            raise ValueError(f"iterations must be in (0, {MAX_ITERATIONS}]")
        if not isinstance(self.salt_bytes, int) or self.salt_bytes < 8:
            raise ValueError("salt_bytes must be int >= 8")
        if not isinstance(self.dklen, int) or self.dklen < 16:
            raise ValueError("dklen must be int >= 16")


# 当前硬件时代的参数
DEFAULT_PARAMS = Params()

# 模拟旧硬件时代的参数（仅用于演示/测试旧记录升级）
LEGACY_PARAMS = Params(iterations=1_000, salt_bytes=8, dklen=32)


class InvalidRecordError(ValueError):
    """记录损坏或无法解析。"""


class VerifyResult(NamedTuple):
    """校验结果。

    valid:         口令是否正确
    upgraded:      本次是否发生了参数升级
    record_before: 校验前的记录（损坏场景以外始终给出）
    record_after:  校验后的记录；未升级时与 record_before 相同
    """

    valid: bool
    upgraded: bool
    record_before: str
    record_after: str


# ---- 记录编解码 -----------------------------------------------------------

def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(text: str, field: str) -> bytes:
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise InvalidRecordError(f"invalid base64 in {field}: {text!r}") from exc


def _build_record(algorithm: str, iterations: int, salt: bytes, digest: bytes) -> str:
    return "$".join((algorithm, str(iterations), _b64e(salt), _b64e(digest)))


def _parse_record(record: str) -> tuple[str, int, bytes, bytes]:
    if not isinstance(record, str):
        raise InvalidRecordError("record must be str")
    parts = record.split("$")
    if len(parts) != 4 or not all(parts):
        raise InvalidRecordError(f"malformed record: {record!r}")

    algorithm, iter_text, salt_b64, hash_b64 = parts
    if algorithm not in _SUPPORTED_ALGORITHMS:
        raise InvalidRecordError(f"unknown algorithm: {algorithm!r}")

    if not iter_text.isascii() or not iter_text.isdigit():
        raise InvalidRecordError(f"iterations must be positive decimal: {iter_text!r}")
    iterations = int(iter_text)
    if not (0 < iterations <= MAX_ITERATIONS):
        raise InvalidRecordError(f"iterations out of range: {iterations}")

    salt = _b64d(salt_b64, "salt")
    digest = _b64d(hash_b64, "hash")
    if not salt:
        raise InvalidRecordError("empty salt")
    if not digest:
        raise InvalidRecordError("empty hash")
    return algorithm, iterations, salt, digest


# ---- 公开 API -------------------------------------------------------------

def _encode_password(password: str) -> bytes:
    if not isinstance(password, str):
        raise TypeError("password must be str")
    if password == "":
        raise ValueError("password must not be empty")
    return password.encode("utf-8")


def _pbkdf2(algorithm: str, iterations: int, salt: bytes,
            password_bytes: bytes, dklen: int) -> bytes:
    return hashlib.pbkdf2_hmac(
        _SUPPORTED_ALGORITHMS[algorithm], password_bytes, salt, iterations, dklen
    )


def hash_password(password: str, params: Params = DEFAULT_PARAMS) -> str:
    """按给定参数生成新记录；盐逐用户随机生成（secrets，CSPRNG）。"""
    params.validate()
    password_bytes = _encode_password(password)
    salt = secrets.token_bytes(params.salt_bytes)
    digest = _pbkdf2(
        params.algorithm, params.iterations, salt,
        password_bytes, params.dklen,
    )
    return _build_record(params.algorithm, params.iterations, salt, digest)


def needs_upgrade(record: str, current: Params = DEFAULT_PARAMS) -> bool:
    """记录内嵌参数是否落后/不同于当前策略。"""
    algorithm, iterations, salt, digest = _parse_record(record)
    return (
        algorithm != current.algorithm
        or iterations != current.iterations
        or len(salt) != current.salt_bytes
        or len(digest) != current.dklen
    )


def verify_password(
    password: str,
    record: str,
    current: Params = DEFAULT_PARAMS,
) -> VerifyResult:
    """按记录内嵌参数校验口令；成功且参数过旧时就地升级。

    - 校验只依赖记录里的算法与参数，不使用全局/当前参数重算旧记录；
    - 口令错误：valid=False，记录原样返回，绝不升级；
    - 记录损坏：抛 InvalidRecordError；
    - 校验成功且参数落后：用 current 参数与“新随机盐”重哈希并返回新记录。
    """
    current.validate()
    algorithm, iterations, salt, digest = _parse_record(record)

    password_bytes = _encode_password(password)
    candidate = _pbkdf2(algorithm, iterations, salt,
                        password_bytes, len(digest))
    valid = hmac.compare_digest(candidate, digest)
    if not valid:
        return VerifyResult(False, False, record, record)

    if needs_upgrade(record, current):
        new_record = hash_password(password, current)
        return VerifyResult(True, True, record, new_record)
    return VerifyResult(True, False, record, record)


# ---- 演示：打印升级前后记录与校验结果 --------------------------------------

def _demo() -> None:
    password = "hunter2-correct-horse"

    # 旧时代生成的记录（低迭代次数、短盐）
    old_record = hash_password(password, LEGACY_PARAMS)
    print("== 旧参数记录（登录前） ==")
    print(old_record)

    # 错误口令：不得升级
    bad = verify_password("wrong-password", old_record)
    print("\n== 错误口令校验 ==")
    print(f"valid={bad.valid} upgraded={bad.upgraded}")
    print(f"record_after == record_before: {bad.record_after == bad.record_before}")

    # 正确口令登录：按旧参数校验成功，并就地升级到当前参数
    result = verify_password(password, old_record)
    print("\n== 正确口令登录（触发升级） ==")
    print(f"valid={result.valid} upgraded={result.upgraded}")
    print("[before]", result.record_before)
    print("[after] ", result.record_after)
    print("\n== 用升级后的记录再次登录（幂等，不再升级） ==")
    again = verify_password(password, result.record_after)
    print(f"valid={again.valid} upgraded={again.upgraded}")
    print(f"record_after == record_before: {again.record_after == again.record_before}")


if __name__ == "__main__":
    _demo()
