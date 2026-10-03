"""SMTP 投递会话状态机（RFC 5321；STARTTLS 见 RFC 3207）。

状态推进完全由「完整读完的应答」与 EHLO 通告的扩展能力驱动：

    CONNECT --220--> EHLO --250--> [STARTTLS --220--> EHLO] --> MAIL
    MAIL --250--> RCPT --250/251--> DATA --354--> CONTENT --250--> QUIT --> DONE

- 任意阶段收到 4xx/421  -> TransientFailure（可退避重试）
- 任意阶段收到 5xx      -> PermanentFailure（立刻中止，附应答依据）
- MAIL 收到 530         -> 对端要求加密：若可升级则 STARTTLS 后重发 MAIL
- 超时 / 断连 / 协议错误 -> 关闭传输、释放资源后抛错
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Union

from .errors import (ConnectionLost, PermanentFailure, ProtocolError,
                     SessionTimeout, SmtpDeliveryError, TransientFailure)
from .policy import DeliveryPolicy, TlsMode
from .replies import SmtpReply, read_reply


class Stage(enum.Enum):
    CONNECT = "CONNECT"      # 等待 220 问候
    EHLO = "EHLO"            # EHLO/HELO 握手，解析扩展能力
    STARTTLS = "STARTTLS"    # 升级加密通道
    MAIL = "MAIL"            # MAIL FROM
    RCPT = "RCPT"            # RCPT TO
    DATA = "DATA"            # DATA 命令
    CONTENT = "CONTENT"      # 发送信体并等待最终应答
    QUIT = "QUIT"
    DONE = "DONE"
    ABORTED = "ABORTED"


@dataclass
class SessionEvent:
    """状态机事件：进入某阶段 / 收到某应答 / 备注。供日志与测试回放。"""

    stage: Stage
    reply: Optional[SmtpReply] = None
    note: str = ""


class SmtpSession:
    """单次连接上的完整投递会话。构造后调用 run() 推进状态机。"""

    def __init__(self, transport, *, sender: str,
                 recipients: Sequence[str],
                 message: Union[str, bytes],
                 policy: DeliveryPolicy,
                 clock,
                 deadline: float,
                 server_name: str = "localhost",
                 on_event: Optional[Callable[[SessionEvent], None]] = None):
        self._transport = transport
        self._sender = sender
        self._recipients = list(recipients)
        self._message = message
        self._policy = policy
        self._clock = clock
        self._deadline = deadline
        self._server_name = server_name
        self._on_event = on_event

        self.stage = Stage.CONNECT
        self.tls_active = False
        self.extensions: Dict[str, str] = {}
        self._used_helo_fallback = False
        self._closed = False

        if hasattr(transport, "set_deadline"):
            transport.set_deadline(deadline)

    # ------------------------------------------------------------------
    # 基础设施
    # ------------------------------------------------------------------
    def _check_deadline(self) -> None:
        if self._clock.monotonic() >= self._deadline:
            raise SessionTimeout("会话整体超时：超过单次会话时间预算")

    def _emit(self, reply: Optional[SmtpReply] = None, note: str = "") -> None:
        if self._on_event is not None:
            self._on_event(SessionEvent(stage=self.stage, reply=reply, note=note))

    def _enter(self, stage: Stage, note: str = "") -> None:
        self.stage = stage
        self._emit(note=note)

    def _read_reply(self) -> SmtpReply:
        self._check_deadline()
        reply = read_reply(self._transport.readline)
        self._emit(reply=reply)
        return reply

    def _send_line(self, line: str) -> None:
        self._check_deadline()
        self._transport.send(line.encode("utf-8") + b"\r\n")

    def _command(self, line: str, expect, what: str) -> SmtpReply:
        self._send_line(line)
        reply = self._read_reply()
        self._classify(reply, expect, what)
        return reply

    def _classify(self, reply: SmtpReply, expect, what: str) -> None:
        if reply.code in expect:
            return
        if reply.is_transient:
            raise TransientFailure(
                f"{what} 被临时拒绝: {reply.describe()}", reply=reply)
        if reply.is_permanent:
            raise PermanentFailure(
                f"{what} 被永久拒绝，立刻中止: {reply.describe()}", reply=reply)
        raise ProtocolError(f"{what} 收到非法应答: {reply.describe()}")

    # ------------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------------
    def run(self) -> None:
        """推进状态机直到 DONE；任何失败都保证关闭传输、释放资源。"""
        try:
            self._run()
        except SmtpDeliveryError as exc:
            exc.stage = self.stage
            self.stage = Stage.ABORTED
            self._emit(note=f"会话中止: {exc}")
            raise
        except TimeoutError as exc:
            wrapped = SessionTimeout(f"会话整体超时: {exc}")
            wrapped.stage = self.stage
            self.stage = Stage.ABORTED
            self._emit(note=f"会话中止: {wrapped}")
            raise wrapped from exc
        finally:
            self._close_transport()

    def _close_transport(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._transport.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 状态机主体
    # ------------------------------------------------------------------
    def _run(self) -> None:
        self._greeting()
        self._ehlo_with_fallback()
        if self._should_starttls():
            self._starttls()
            if self.tls_active:
                # RFC 3207 §4.2：TLS 建立后必须丢弃此前状态，重新 EHLO
                self._ehlo_with_fallback()
        elif self._policy.tls is TlsMode.REQUIRE and not self.tls_active:
            raise TransientFailure(
                "对端未通告 STARTTLS，而本地策略要求加密通道，稍后重试")
        self._transaction()
        self._quit()
        self._enter(Stage.DONE)

    def _greeting(self) -> None:
        self._enter(Stage.CONNECT)
        reply = self._read_reply()
        if reply.code == 220:
            return
        if reply.is_transient:  # 含 421「服务不可用，关闭传输通道」
            raise TransientFailure(
                f"对端问候即临时拒绝服务: {reply.describe()}", reply=reply)
        if reply.is_permanent:
            raise PermanentFailure(
                f"对端问候即永久拒绝服务，立刻中止: {reply.describe()}", reply=reply)
        raise ProtocolError(f"问候阶段收到非法应答: {reply.describe()}")

    def _ehlo_with_fallback(self) -> None:
        self._enter(Stage.EHLO)
        self._send_line(f"EHLO {self._policy.helo_name}")
        reply = self._read_reply()
        if reply.code == 250:
            self.extensions = self._parse_extensions(reply)
            return
        if (reply.is_permanent and self._policy.ehlo_fallback
                and not self._used_helo_fallback):
            self._used_helo_fallback = True
            self._emit(note="EHLO 被永久拒绝，退回 HELO 重试一次")
            self._send_line(f"HELO {self._policy.helo_name}")
            helo = self._read_reply()
            if helo.code == 250:
                self.extensions = {}
                return
            self._classify(helo, {250}, "HELO")
        self._classify(reply, {250}, "EHLO")

    @staticmethod
    def _parse_extensions(reply: SmtpReply) -> Dict[str, str]:
        """EHLO 成功应答：首行是域名，其余每行一个扩展（关键字 + 可选参数）。"""
        extensions: Dict[str, str] = {}
        for line in reply.lines[1:]:
            parts = line.split(None, 1)
            if parts:
                extensions[parts[0].upper()] = parts[1] if len(parts) > 1 else ""
        return extensions

    def _should_starttls(self) -> bool:
        if self.tls_active or self._policy.tls is TlsMode.OFF:
            return False
        return "STARTTLS" in self.extensions

    def _starttls(self) -> None:
        self._enter(Stage.STARTTLS)
        self._send_line("STARTTLS")
        reply = self._read_reply()
        if reply.code == 220:
            self._check_deadline()
            self._transport.start_tls(server_hostname=self._server_name)
            self.tls_active = True
            self._emit(note="通道已升级为 TLS")
            return
        if self._policy.tls is TlsMode.REQUIRE:
            raise TransientFailure(
                f"策略要求加密，但 STARTTLS 协商失败: {reply.describe()}",
                reply=reply)
        # PREFER：加密暂时不可用，退回明文继续
        self._emit(note=f"STARTTLS 被拒（{reply.describe()}），按 prefer 策略退回明文")

    def _transaction(self) -> None:
        try:
            self._mail_from()
        except PermanentFailure as exc:
            # 530：对端要求先换用加密通道（RFC 3207 / RFC 4954 风格应答）
            if (exc.reply is not None and exc.reply.code == 530
                    and self._try_mid_session_starttls()):
                self._mail_from()  # 升级成功后重发 MAIL FROM
            else:
                raise
        self._rcpt_to()
        self._data()

    def _try_mid_session_starttls(self) -> bool:
        if self.tls_active or self._policy.tls is TlsMode.OFF:
            return False
        if "STARTTLS" not in self.extensions:
            return False
        self._emit(note="对端要求加密（530），在会话中途升级通道")
        self._starttls()
        if not self.tls_active:
            return False
        self._ehlo_with_fallback()
        return True

    def _mail_from(self) -> None:
        self._enter(Stage.MAIL)
        self._command(f"MAIL FROM:<{self._sender}>", {250}, "MAIL FROM")

    def _rcpt_to(self) -> None:
        self._enter(Stage.RCPT)
        for recipient in self._recipients:
            self._send_line(f"RCPT TO:<{recipient}>")
            reply = self._read_reply()
            if reply.code in (250, 251):
                continue
            if reply.is_transient:
                raise TransientFailure(
                    f"收件人 <{recipient}> 被临时拒绝: {reply.describe()}",
                    reply=reply)
            if reply.is_permanent:
                raise PermanentFailure(
                    f"收件人 <{recipient}> 被永久拒绝，立刻中止: {reply.describe()}",
                    reply=reply)
            raise ProtocolError(
                f"RCPT TO 收到非法应答: {reply.describe()}")

    def _data(self) -> None:
        self._enter(Stage.DATA)
        self._command("DATA", {354}, "DATA")
        self._enter(Stage.CONTENT)
        self._send_content()
        reply = self._read_reply()
        self._classify(reply, {250}, "信体投递")

    def _send_content(self) -> None:
        """发送信体：规范换行、按 RFC 5321 §4.5.2 做点填充，以 <CRLF>.<CRLF> 结束。"""
        data = self._message
        if isinstance(data, str):
            data = data.encode("utf-8")
        data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        stuffed = []
        for line in data.split(b"\n"):
            if line.startswith(b"."):
                line = b"." + line
            stuffed.append(line)
        payload = b"\r\n".join(stuffed) + b"\r\n.\r\n"
        self._check_deadline()
        self._transport.send(payload)

    def _quit(self) -> None:
        self._enter(Stage.QUIT)
        try:
            self._send_line("QUIT")
            self._read_reply()  # 期望 221；对端不答不影响投递结果
        except (SmtpDeliveryError, TimeoutError):
            self._emit(note="QUIT 未获正常应答，忽略（信体已被接收）")
