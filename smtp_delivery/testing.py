"""测试/演示支持：脚本化假对端（实现 Transport 接口），全程确定性、不碰网络。

脚本是由 readline() 按顺序消费的步骤列表：

  ("reply", "...")  向客户端推送一段应答文本；可含多行，用 \\r\\n 分隔
  ("stall", 秒)     对端卡顿：把假时钟向前推进；越过会话 deadline 即触发超时
  ("close",)        对端直接关闭连接（下一次 readline 返回 b""）

客户端发出的所有字节记录在 sent 列表里，供断言命令序列与点填充结果。
"""

from __future__ import annotations

from typing import Callable, Iterable, List, Optional


class ScriptedServer:
    def __init__(self, script: Iterable, clock):
        self.script: List = list(script)
        self.clock = clock
        self.sent: List[bytes] = []
        self.closed = False
        self.tls_started = False
        self.deadline: Optional[float] = None
        self._inbound = b""

    def set_deadline(self, deadline: float) -> None:
        self.deadline = deadline

    def _check_deadline(self) -> None:
        if self.deadline is not None and self.clock.monotonic() >= self.deadline:
            raise TimeoutError("ScriptedServer: 会话 deadline 已到")

    def readline(self) -> bytes:
        self._check_deadline()
        while b"\n" not in self._inbound:
            if not self.script:
                raise AssertionError("脚本步骤已耗尽，但客户端仍在等待应答")
            step = self.script.pop(0)
            kind = step[0]
            if kind == "reply":
                self._inbound += step[1].encode("utf-8")
            elif kind == "stall":
                self.clock.sleep(step[1])
                self._check_deadline()
            elif kind == "close":
                return b""
            else:
                raise AssertionError(f"未知脚本步骤: {step!r}")
        line, self._inbound = self._inbound.split(b"\n", 1)
        return line + b"\n"

    def send(self, data: bytes) -> None:
        self._check_deadline()
        self.sent.append(bytes(data))

    def start_tls(self, server_hostname: str = "localhost") -> None:
        self._check_deadline()
        self.tls_started = True

    def close(self) -> None:
        self.closed = True

    def sent_text(self) -> str:
        return b"".join(self.sent).decode("utf-8", "replace")


def server_factory(scripts: Iterable, clock,
                   created: Optional[List[ScriptedServer]] = None) -> Callable:
    """生成 deliver() 需要的 transport_factory；每次尝试消费一个脚本。"""
    scripts_iter = iter(scripts)

    def factory() -> ScriptedServer:
        server = ScriptedServer(next(scripts_iter), clock)
        if created is not None:
            created.append(server)
        return server

    return factory
