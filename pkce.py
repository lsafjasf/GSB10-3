"""PKCE 授权码校验库（RFC 7636 风格），仅依赖 Python 标准库。

核心思想：授权请求与换令牌请求通过一次性的校验值绑定——
  1. 客户端生成 code_verifier（CSPRNG 随机），授权请求只带其派生值 code_challenge；
  2. 服务端签发授权码时记录 (code_challenge, challenge_method)；
  3. 换令牌时必须出示 code_verifier，服务端重新计算 challenge 并做常量时间比对；
  4. 授权码一次性使用，任何兑换尝试（成功或失败）都会消耗它并留下审计记录。

随机性来源：secrets 模块（底层为 os.urandom，即操作系统 CSPRNG），
不使用 random 等可预测伪随机数生成器。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

# --- 长度与字符集约束（RFC 7636 §4.1）---
VERIFIER_MIN_LEN = 43
VERIFIER_MAX_LEN = 128
# 默认生成 48 字节随机数 -> base64url 编码后 64 字符，落在 [43, 128] 内
VERIFIER_RANDOM_BYTES = 48
# 授权码本身：32 字节 CSPRNG 随机数 -> 43 字符 base64url
CODE_RANDOM_BYTES = 32
# 授权码默认有效期（秒）
DEFAULT_TTL_SECONDS = 600

_VERIFIER_RE = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")

SUPPORTED_METHODS = ("S256", "plain")


class Reason:
    """兑换结果的机器可读原因码。"""

    OK = "OK"
    CODE_NOT_FOUND = "CODE_NOT_FOUND"                # 授权码不存在
    CODE_ALREADY_USED = "CODE_ALREADY_USED"          # 重复兑换（一次性断言）
    CODE_EXPIRED = "CODE_EXPIRED"                    # 授权码过期
    VERIFIER_MISSING = "VERIFIER_MISSING"            # 未提供校验值
    VERIFIER_MALFORMED = "VERIFIER_MALFORMED"        # 校验值长度/字符集不合法
    CHALLENGE_METHOD_UNSUPPORTED = "CHALLENGE_METHOD_UNSUPPORTED"  # 算法不一致/不支持
    VERIFIER_MISMATCH = "VERIFIER_MISMATCH"          # 校验值与授权请求绑定值不匹配


_REASON_MESSAGES = {
    Reason.OK: "兑换成功",
    Reason.CODE_NOT_FOUND: "授权码不存在",
    Reason.CODE_ALREADY_USED: "授权码已被使用，一次性授权码禁止重复兑换",
    Reason.CODE_EXPIRED: "授权码已过期",
    Reason.VERIFIER_MISSING: "缺少 code_verifier",
    Reason.VERIFIER_MALFORMED: "code_verifier 长度或字符集不合法（须为 43-128 个 unreserved 字符）",
    Reason.CHALLENGE_METHOD_UNSUPPORTED: "code_challenge_method 不在服务端支持的算法列表中",
    Reason.VERIFIER_MISMATCH: "code_verifier 与授权请求绑定的 code_challenge 不匹配",
}


def generate_code_verifier(num_bytes: int = VERIFIER_RANDOM_BYTES) -> str:
    """生成 code_verifier。

    随机性来源：secrets.token_urlsafe -> os.urandom（操作系统 CSPRNG）。
    默认 48 字节 -> 64 个 base64url 字符，满足 RFC 7636 的 43-128 长度要求。
    """
    verifier = secrets.token_urlsafe(num_bytes)
    if not (VERIFIER_MIN_LEN <= len(verifier) <= VERIFIER_MAX_LEN):
        raise ValueError("num_bytes 须使编码后长度落在 43-128 之间")
    return verifier


def is_valid_verifier(verifier: str) -> bool:
    """校验 code_verifier 是否符合 RFC 7636 语法（43-128 个 unreserved 字符）。"""
    return isinstance(verifier, str) and bool(_VERIFIER_RE.match(verifier))


def compute_challenge(verifier: str, method: str = "S256") -> str:
    """由 code_verifier 计算 code_challenge。

    S256: base64url(sha256(verifier))，去掉填充 '='（RFC 7636 §4.2）。
    plain: 原样返回（仅用于兼容旧客户端，新系统应强制 S256）。
    """
    if method == "S256":
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    if method == "plain":
        return verifier
    raise ValueError(f"不支持的 code_challenge_method: {method!r}")


def generate_authorization_code(num_bytes: int = CODE_RANDOM_BYTES) -> str:
    """生成授权码本身：32 字节 CSPRNG 随机数 -> 43 字符 base64url。"""
    return secrets.token_urlsafe(num_bytes)


@dataclass
class CodeRecord:
    """服务端为每个授权码保存的记录。"""

    code: str
    challenge: str
    method: str
    issued_at: float
    ttl: float
    consumed: bool = False

    def is_expired(self, now: float) -> bool:
        return now >= self.issued_at + self.ttl


@dataclass
class RejectionRecord:
    """一次被拒绝的兑换尝试的审计记录。"""

    reason: str
    code_prefix: str  # 只保留前 8 位，避免日志泄露完整授权码
    at: float
    detail: str = ""


@dataclass
class ExchangeResult:
    ok: bool
    reason: str
    message: str


class AuthorizationCodeStore:
    """授权码的签发与一次性兑换。

    clock 可注入（默认 time.time），便于测试过期场景。
    所有被拒绝的兑换都会追加到 rejections，供审计与告警。
    """

    def __init__(
        self,
        ttl: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
        allowed_methods: Tuple[str, ...] = SUPPORTED_METHODS,
    ) -> None:
        self.ttl = ttl
        self._clock = clock
        self.allowed_methods = tuple(allowed_methods)
        self._records: Dict[str, CodeRecord] = {}
        self.rejections: List[RejectionRecord] = []

    # --- 签发侧（授权端点）---

    def issue(
        self,
        challenge: str,
        method: str = "S256",
        ttl: Optional[float] = None,
    ) -> str:
        """为一次授权请求签发授权码，绑定 (challenge, method)。"""
        if method not in self.allowed_methods:
            raise ValueError(f"不支持的 code_challenge_method: {method!r}")
        if not challenge:
            raise ValueError("challenge 不能为空")
        code = generate_authorization_code()
        self._records[code] = CodeRecord(
            code=code,
            challenge=challenge,
            method=method,
            issued_at=self._clock(),
            ttl=self.ttl if ttl is None else ttl,
        )
        return code

    def import_record(self, record: CodeRecord) -> None:
        """导入外部持久化的记录（例如从数据库重建）。"""
        self._records[record.code] = record

    # --- 兑换侧（令牌端点）---

    def exchange(self, code: Optional[str], verifier: Optional[str]) -> ExchangeResult:
        """用授权码 + code_verifier 兑换令牌。授权码一次性使用。"""
        if not code or code not in self._records:
            return self._reject(code, Reason.CODE_NOT_FOUND)

        record = self._records[code]
        now = self._clock()

        # 一次性断言：无论这次兑换能否成功，先检查是否已被消费；
        # 已消费的码即使参数完全正确也必须拒绝。
        if record.consumed:
            return self._reject(code, Reason.CODE_ALREADY_USED)

        # 先消耗再校验：失败的重试不能反复试探同一个码。
        record.consumed = True

        if record.is_expired(now):
            return self._reject(code, Reason.CODE_EXPIRED)

        if verifier is None or verifier == "":
            return self._reject(code, Reason.VERIFIER_MISSING)

        if not is_valid_verifier(verifier):
            return self._reject(code, Reason.VERIFIER_MALFORMED)

        # 算法一致性：授权请求声明的 method 必须仍在服务端允许列表中，
        # 否则无法确定用哪种算法重算 challenge，直接拒绝。
        if record.method not in self.allowed_methods:
            return self._reject(code, Reason.CHALLENGE_METHOD_UNSUPPORTED)

        expected = compute_challenge(verifier, record.method)
        # 常量时间比对，避免通过计时侧信道逐字节探测 challenge。
        if not hmac.compare_digest(expected, record.challenge):
            return self._reject(code, Reason.VERIFIER_MISMATCH)

        return ExchangeResult(ok=True, reason=Reason.OK, message=_REASON_MESSAGES[Reason.OK])

    def _reject(self, code: Optional[str], reason: str) -> ExchangeResult:
        prefix = (code or "")[:8]
        self.rejections.append(
            RejectionRecord(
                reason=reason,
                code_prefix=prefix,
                at=self._clock(),
                detail=_REASON_MESSAGES[reason],
            )
        )
        return ExchangeResult(ok=False, reason=reason, message=_REASON_MESSAGES[reason])


def begin_authorization(
    store: AuthorizationCodeStore,
    method: str = "S256",
    ttl: Optional[float] = None,
) -> Tuple[str, str]:
    """便捷函数：生成 verifier 并签发授权码，返回 (code, verifier)。

    模拟客户端流程：本地持有 verifier，授权请求只发送 challenge。
    """
    verifier = generate_code_verifier()
    code = store.issue(compute_challenge(verifier, method), method, ttl=ttl)
    return code, verifier
