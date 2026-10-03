"""SMTP 投递会话状态机（仅标准库）。

设计要点：
- 应答按 RFC 5321 完整读取多行（`250-...` 直到 `250 ...`），读完后才判断。
- 时间可注入：`now_fn` / `sleep_fn`，测试用假时钟，生产用 time.monotonic/sleep。
- 会话有整体超时（session_timeout），超时即中止并释放传输层资源。
- 4xx 临时拒绝 -> 按指数退避重试；5xx 永久拒绝 -> 立即中止并给出依据。
- 支持 STARTTLS：能力声明后主动升级，或投递中途被 530 要求升级。
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol, Sequence


# ---------------------------------------------------------------- 传输层抽象

class TransportTimeout(Exception):
    """传输层读超时（由 Transport 实现抛出）。"""


class Transport(Protocol):
    """底层连接抽象。生产实现可包装 socket/ssl，测试用脚本化替身。"""

    def readline(self, timeout: Optional[float] = None) -> str:
        """读一行（不含 CRLF）。超时须抛 TransportTimeout。"""
        ...

    def sendline(self, line: str) -> None:
        ...

    def starttls(self) -> None:
        """在现有连接上升级为加密通道。"""
        ...

    def close(self) -> None:
        ...


# ---------------------------------------------------------------- 应答解析

class ProtocolError(Exception):
    """对端应答不符合协议（按临时失败处理，可重试）。"""


@dataclass
class Reply:
    code: int
    lines: list  # 每行去掉 "nnn " / "nnn-" 前缀后的文本

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def is_temp(self) -> bool:
        return 400 <= self.code <= 499

    @property
    def is_perm(self) -> bool:
        return 500 <= self.code <= 599


def parse_reply_line(raw: str):
    """返回 (code, is_last, text)。非法行抛 ProtocolError。"""
    if len(raw) < 4 or not raw[:3].isdigit():
        raise ProtocolError(f"应答行格式非法: {raw!r}")
    sep = raw[3]
    if sep == "-":
        return int(raw[:3]), False, raw[4:]
    if sep == " ":
        return int(raw[:3]), True, raw[4:]
    raise ProtocolError(f"应答行分隔符非法: {raw!r}")


# ---------------------------------------------------------------- 状态与结果

class State(enum.Enum):
    CONNECT = "CONNECT"        # 已连接，等待 220
    GREETED = "GREETED"        # 收到 220
    EHLO = "EHLO"              # EHLO 成功，能力已知
    TLS = "TLS"                # 已升级加密通道
    MAIL = "MAIL"              # MAIL FROM 被接受
    RCPT = "RCPT"              # 至少一个 RCPT TO 被接受
    DATA = "DATA"              # 354，正在发正文
    DONE = "DONE"              # 正文被接受
    ABORT = "ABORT"            # 异常中止


class Outcome(enum.Enum):
    DELIVERED = "delivered"
    TEMP_FAILURE = "temp_failure"        # 4xx，可重试
    PERM_FAILURE = "perm_failure"        # 5xx，不可重试
    TIMEOUT = "timeout"                  # 整体超时，可重试
    PROTOCOL_ERROR = "protocol_error"    # 协议错误，可重试


@dataclass
class Transition:
    from_state: State
    to_state: State
    event: str
    reply_code: Optional[int] = None


@dataclass
class AttemptResult:
    outcome: Outcome
    reason: str
    transitions: list


@dataclass
class RetryRecord:
    attempt: int      # 刚失败的是第几次尝试（1 起）
    delay: float      # 距下次尝试的退避秒数
    reason: str       # 失败依据（含应答码）


@dataclass
class DeliveryResult:
    status: str                 # "delivered" | "failed"
    reason: str
    attempts: int
    retry_log: list             # list[RetryRecord]
    transitions: list           # 所有尝试的 Transition 汇总


@dataclass
class Message:
    sender: str
    recipients: Sequence[str]
    body: str                   # 完整报文（含头部），\n 分行


@dataclass
class DeliveryConfig:
    helo_name: str = "localhost"
    session_timeout: float = 60.0   # 单次会话整体超时（秒）
    max_attempts: int = 5
    backoff_base: float = 1.0       # 退避基数（秒）
    backoff_cap: float = 60.0       # 退避上限（秒）
    require_tls: bool = False       # 策略：必须加密才投递
    backoff_fn: Optional[Callable[[int], float]] = None  # 自定义退避，参数为失败次数


class SessionTimeout(Exception):
    pass


# ---------------------------------------------------------------- 会话状态机

class DeliverySession:
    """一次连接上的完整投递会话。"""

    def __init__(self, transport: Transport, message: Message,
                 config: DeliveryConfig, now_fn: Callable[[], float]):
        self.transport = transport
        self.message = message
        self.config = config
        self._now = now_fn
        self._deadline = now_fn() + config.session_timeout
        self.state = State.CONNECT
        self.transitions: list = []
        self._caps: set = set()
        self._tls_active = False
        self._timed_out = False

    # -- 对外入口 ----------------------------------------------------

    def run(self) -> AttemptResult:
        try:
            return self._run()
        except SessionTimeout as exc:
            self._timed_out = True
            self._to(State.ABORT, "session-timeout")
            return AttemptResult(Outcome.TIMEOUT, str(exc), self.transitions)
        except ProtocolError as exc:
            self._to(State.ABORT, "protocol-error")
            return AttemptResult(Outcome.PROTOCOL_ERROR, str(exc), self.transitions)
        finally:
            # 无论成败都必须释放资源；超时的连接不再礼貌 QUIT，直接关。
            if not self._timed_out:
                try:
                    self._quit()
                except Exception:
                    pass
            try:
                self.transport.close()
            except Exception:
                pass

    # -- 主流程 ------------------------------------------------------

    def _run(self) -> AttemptResult:
        greeting = self._read_reply()
        if greeting.code != 220:
            return self._fail("greeting", greeting)
        self._to(State.GREETED, "220 greeting", greeting.code)

        result = self._ehlo()
        if result is not None:
            return result

        if self.config.require_tls and "STARTTLS" not in self._caps:
            return AttemptResult(
                Outcome.PERM_FAILURE,
                "策略要求加密投递，但对端未声明 STARTTLS 能力", self.transitions)
        if self.config.require_tls:
            result = self._starttls()
            if result is not None:
                return result

        # MAIL FROM：可能被 530 要求先升级加密通道
        while True:
            mail = self._command(f"MAIL FROM:<{self.message.sender}>")
            if mail.code == 530 and self._should_upgrade_tls(mail):
                result = self._starttls()
                if result is not None:
                    return result
                continue
            if mail.code != 250:
                return self._fail("MAIL FROM", mail)
            break
        self._to(State.MAIL, "250 sender ok", mail.code)

        for rcpt in self.message.recipients:
            r = self._command(f"RCPT TO:<{rcpt}>")
            if r.code != 250:
                return self._fail("RCPT TO", r)
        self._to(State.RCPT, "250 recipient ok", 250)

        data = self._command("DATA")
        if data.code != 354:
            return self._fail("DATA", data)
        self._to(State.DATA, "354 send body", data.code)

        for line in self.message.body.split("\n"):
            line = line.rstrip("\r")
            if line.startswith("."):           # dot-stuffing，防止提前终止
                line = "." + line
            self._check_deadline()
            self.transport.sendline(line)
        self.transport.sendline(".")

        final = self._read_reply()
        if final.code != 250:
            return self._fail("message body", final)
        self._to(State.DONE, "250 queued", final.code)
        return AttemptResult(Outcome.DELIVERED, "250 投递成功", self.transitions)

    # -- 子步骤 ------------------------------------------------------

    def _ehlo(self) -> Optional[AttemptResult]:
        reply = self._command(f"EHLO {self.config.helo_name}")
        if reply.code != 250:
            return self._fail("EHLO", reply)
        self._caps = {ln.split()[0].upper() for ln in reply.lines if ln.split()}
        self._to(State.EHLO, "250 capabilities", reply.code)
        return None

    def _should_upgrade_tls(self, reply: Reply) -> bool:
        return (not self._tls_active
                and "STARTTLS" in self._caps
                and "STARTTLS" in reply.text.upper())

    def _starttls(self) -> Optional[AttemptResult]:
        reply = self._command("STARTTLS")
        if reply.code != 220:
            return self._fail("STARTTLS", reply)
        self.transport.starttls()
        self._tls_active = True
        self._to(State.TLS, "220 tls ready", reply.code)
        return self._ehlo()  # RFC 3207：升级后必须重新 EHLO

    def _quit(self) -> None:
        self.transport.sendline("QUIT")
        self._read_reply()

    def _fail(self, stage: str, reply: Reply) -> AttemptResult:
        reason = f"{stage} 被拒: {reply.code} {reply.text}"
        if reply.is_temp:
            outcome = Outcome.TEMP_FAILURE
        elif reply.is_perm:
            outcome = Outcome.PERM_FAILURE
        else:
            outcome = Outcome.PROTOCOL_ERROR
        return AttemptResult(outcome, reason, self.transitions)

    # -- 底层读写 ----------------------------------------------------

    def _to(self, state: State, event: str, code: Optional[int] = None) -> None:
        self.transitions.append(Transition(self.state, state, event, code))
        self.state = state

    def _check_deadline(self) -> None:
        if self._now() >= self._deadline:
            raise SessionTimeout(
                f"会话超过整体超时 {self.config.session_timeout}s，已中止")

    def _read_line(self) -> str:
        self._check_deadline()
        remaining = self._deadline - self._now()
        try:
            return self.transport.readline(timeout=remaining)
        except TransportTimeout as exc:
            raise SessionTimeout(
                f"会话超过整体超时 {self.config.session_timeout}s，已中止") from exc

    def _read_reply(self) -> Reply:
        """完整读取多行应答后再返回，绝不只看第一行。"""
        code: Optional[int] = None
        lines: list = []
        while True:
            this_code, is_last, text = parse_reply_line(self._read_line())
            if code is None:
                code = this_code
            elif this_code != code:
                raise ProtocolError(
                    f"多行应答应答码不一致: {code} 与 {this_code}")
            lines.append(text)
            if is_last:
                return Reply(code, lines)

    def _command(self, line: str) -> Reply:
        self._check_deadline()
        self.transport.sendline(line)
        return self._read_reply()


# ---------------------------------------------------------------- 重试驱动

def default_backoff(attempt: int, base: float, cap: float) -> float:
    """指数退避：base, 2*base, 4*base, ... 封顶 cap。"""
    return min(base * (2 ** (attempt - 1)), cap)


def deliver(transport_factory: Callable[[], Transport],
            message: Message,
            config: Optional[DeliveryConfig] = None,
            now_fn: Callable[[], float] = time.monotonic,
            sleep_fn: Callable[[float], None] = time.sleep) -> DeliveryResult:
    """带退避重试的投递入口。每次尝试新建连接（transport_factory）。"""
    config = config or DeliveryConfig()
    attempts = 0
    retry_log: list = []
    transitions: list = []

    while True:
        attempts += 1
        session = DeliverySession(transport_factory(), message, config, now_fn)
        result = session.run()
        transitions.extend(result.transitions)

        if result.outcome is Outcome.DELIVERED:
            return DeliveryResult("delivered", result.reason, attempts,
                                  retry_log, transitions)
        if result.outcome is Outcome.PERM_FAILURE:
            return DeliveryResult("failed", f"永久拒绝，立即中止: {result.reason}",
                                  attempts, retry_log, transitions)

        # TEMP_FAILURE / TIMEOUT / PROTOCOL_ERROR 均可重试
        if attempts >= config.max_attempts:
            return DeliveryResult(
                "failed",
                f"已达最大尝试次数 {config.max_attempts}，最后失败: {result.reason}",
                attempts, retry_log, transitions)
        delay = (config.backoff_fn(attempts) if config.backoff_fn
                 else default_backoff(attempts, config.backoff_base,
                                      config.backoff_cap))
        retry_log.append(RetryRecord(attempts, delay, result.reason))
        sleep_fn(delay)
