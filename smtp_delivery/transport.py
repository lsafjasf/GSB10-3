"""传输层：面向行的通道抽象，以及基于 socket/ssl 的真实实现（仅标准库）。

deadline 由会话层通过 set_deadline 注入；每次 I/O 前根据可注入时钟
换算成剩余超时，保证「会话整体超时」能真正生效。
"""

from __future__ import annotations

import socket
import ssl
from typing import Protocol, runtime_checkable

from .clock import RealClock
from .errors import ConnectionLost


@runtime_checkable
class Transport(Protocol):
    def set_deadline(self, deadline: float) -> None: ...
    def readline(self) -> bytes: ...
    def send(self, data: bytes) -> None: ...
    def start_tls(self, server_hostname: str) -> None: ...
    def close(self) -> None: ...


class SocketTransport:
    """真实 SMTP 通道。构造时即发起 TCP 连接。"""

    def __init__(self, host: str, port: int = 25, *,
                 connect_timeout: float = 10.0,
                 tls_context: "ssl.SSLContext | None" = None,
                 clock: "object | None" = None):
        self._clock = clock or RealClock()
        self._deadline = None
        self._host = host
        self._sock = socket.create_connection((host, port), timeout=connect_timeout)
        self._reader = self._sock.makefile("rb")
        self._tls_context = tls_context or ssl.create_default_context()
        self._closed = False

    def set_deadline(self, deadline: float) -> None:
        self._deadline = deadline

    def _arm(self) -> None:
        if self._deadline is None:
            return
        remaining = self._deadline - self._clock.monotonic()
        if remaining <= 0:
            raise TimeoutError("会话整体超时")
        self._sock.settimeout(remaining)

    def readline(self) -> bytes:
        self._arm()
        try:
            return self._reader.readline()
        except socket.timeout as exc:
            raise TimeoutError("等待对端应答超时") from exc
        except OSError as exc:
            raise ConnectionLost(f"读连接出错: {exc}") from exc

    def send(self, data: bytes) -> None:
        self._arm()
        try:
            self._sock.sendall(data)
        except socket.timeout as exc:
            raise TimeoutError("发送数据超时") from exc
        except OSError as exc:
            raise ConnectionLost(f"写连接出错: {exc}") from exc

    def start_tls(self, server_hostname: str) -> None:
        self._arm()
        self._sock = self._tls_context.wrap_socket(
            self._sock, server_hostname=server_hostname)
        self._reader = self._sock.makefile("rb")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for closer in (self._reader.close, self._sock.close):
            try:
                closer()
            except Exception:
                pass
