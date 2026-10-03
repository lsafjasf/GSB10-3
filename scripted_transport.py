"""测试/演示替身：脚本化传输层 + 假时钟（仅标准库）。

ScriptedTransport 按脚本逐步校验命令并给出应答：
- 脚本项 (expect, reply)：expect 是期望收到的命令前缀，reply 是应答行列表；
- expect 为 None 表示无需命令直接给出应答（用于 220 问候）；
- reply 为 "<HANG>" 表示对端挂死：推进时钟并抛 TransportTimeout；
- DATA 之后进入正文收集模式，直到收到 "." 再匹配下一脚本项。
"""

from smtp_delivery import TransportTimeout

HANG = "<HANG>"


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.t = start
        self.sleeps: list = []   # 记录每次 sleep 的时长，用于校验退避数据

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds

    def advance(self, seconds: float) -> None:
        self.t += seconds


class ScriptedTransport:
    def __init__(self, script: list, clock: FakeClock = None):
        self._script = list(script)
        self._clock = clock
        self._pending: list = []       # 已排队待读的应答行
        self._collecting_body = False
        self.sent: list = []           # 实际发出的所有行
        self.closed = False
        self.tls_active = False
        self.starttls_calls = 0
        # 问候语等 expect=None 的前导脚本项直接入队
        while self._script and self._script[0][0] is None:
            _, reply = self._script.pop(0)
            self._queue(reply)

    # -- Transport 接口 ----------------------------------------------

    def readline(self, timeout=None) -> str:
        if not self._pending:
            raise AssertionError("对端没有更多应答：是否没有完整读完多行应答？")
        line = self._pending.pop(0)
        if line == HANG:
            if self._clock is not None:
                self._clock.advance(10 ** 6)   # 挂死：时间直接越过整体超时
            raise TransportTimeout("对端无响应")
        return line

    def sendline(self, line: str) -> None:
        self.sent.append(line)
        if self._collecting_body:
            if line == ".":
                self._collecting_body = False
                self._match_and_queue(line)
            return
        self._match_and_queue(line)
        if line == "DATA":
            self._collecting_body = True

    def starttls(self) -> None:
        self.starttls_calls += 1
        self.tls_active = True

    def close(self) -> None:
        self.closed = True

    # -- 内部 ----------------------------------------------------------

    def _queue(self, reply) -> None:
        lines = [reply] if isinstance(reply, str) else list(reply)
        self._pending.extend(lines)

    def _match_and_queue(self, command: str) -> None:
        if not self._script:
            raise AssertionError(f"脚本已用完，却收到命令: {command!r}")
        expect, reply = self._script.pop(0)
        if expect is None:
            raise AssertionError(f"脚本项无需命令，却收到: {command!r}")
        assert command.startswith(expect), \
            f"期望命令 {expect!r}，实际 {command!r}"
        self._queue(reply)
