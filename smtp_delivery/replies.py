"""SMTP 应答解析（RFC 5321 §4.2.1）。

应答可能是多行的：
    250-mx.example.net
    250-SIZE 10485760
    250 STARTTLS
判定必须等读到「代码 + 空格」的末行之后才能进行，绝不能只看第一行；
各续行的三位代码还必须与首行一致，否则视为协议错误。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from .errors import ConnectionLost, ProtocolError, SessionTimeout

MAX_REPLY_LINES = 100
MAX_LINE_BYTES = 512  # RFC 5321 §4.5.3.1.1：文本行上限（含 CRLF）


@dataclass
class SmtpReply:
    code: int
    lines: List[str] = field(default_factory=list)
    enhanced: Optional[Tuple[int, int, int]] = None  # RFC 2034 增强码，如 5.1.1

    @property
    def message(self) -> str:
        return " / ".join(self.lines)

    @property
    def is_transient(self) -> bool:
        return self.code // 100 == 4

    @property
    def is_permanent(self) -> bool:
        return self.code // 100 == 5

    def describe(self) -> str:
        enh = ""
        if self.enhanced is not None:
            enh = f" [增强码 {self.enhanced[0]}.{self.enhanced[1]}.{self.enhanced[2]}]"
        return f"{self.code} {self.message}{enh}"


def _parse_enhanced(text: str) -> Optional[Tuple[int, int, int]]:
    token = text.split(None, 1)[0] if text.split() else ""
    parts = token.split(".")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        return int(parts[0]), int(parts[1]), int(parts[2])
    return None


def read_reply(readline: Callable[[], bytes]) -> SmtpReply:
    """循环读取，直到收完整段多行应答。

    readline: 无参可调用对象，返回一行 bytes（含换行）；连接关闭时返回 b""。
    """
    lines: List[str] = []
    code: Optional[int] = None

    while True:
        try:
            raw = readline()
        except TimeoutError as exc:
            raise SessionTimeout("等待对端应答超时") from exc

        if raw == b"":
            raise ConnectionLost("对端在应答中途关闭了连接")
        if len(raw) > MAX_LINE_BYTES:
            raise ProtocolError(f"应答行超长（{len(raw)} 字节，上限 {MAX_LINE_BYTES}）")

        text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if len(text) < 3 or not text[:3].isdigit():
            raise ProtocolError(f"应答行缺少三位数字代码: {text!r}")

        line_code = int(text[:3])
        if code is None:
            code = line_code
        elif line_code != code:
            raise ProtocolError(
                f"多行应答代码不一致：首行 {code}，续行 {line_code}（必须读完整段应答）"
            )

        if len(text) == 3:
            # 允许末行只有 "250\r\n"（RFC 允许省略空格与文本）
            lines.append("")
            break

        separator, rest = text[3], text[4:]
        if separator == "-":
            lines.append(rest)
        elif separator == " ":
            lines.append(rest)
            break
        else:
            raise ProtocolError(f"应答行第 4 个字符必须是 '-' 或空格: {text!r}")

        if len(lines) > MAX_REPLY_LINES:
            raise ProtocolError(f"多行应答超过 {MAX_REPLY_LINES} 行，疑似对端异常")

    enhanced = _parse_enhanced(lines[0]) if lines else None
    return SmtpReply(code=code, lines=lines, enhanced=enhanced)
