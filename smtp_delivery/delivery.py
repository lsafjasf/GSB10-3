"""投递编排：重试、退避、整体超时预算与结果汇总。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Union

from .clock import RealClock
from .errors import ConnectionLost, PermanentFailure, SmtpDeliveryError
from .policy import DeliveryPolicy
from .session import SessionEvent, SmtpSession, Stage

EventType = Callable[[int, SessionEvent], None]


@dataclass
class AttemptRecord:
    attempt: int                 # 第几次尝试（从 1 开始）
    stage: str                   # 失败阶段；成功为 DONE
    outcome: str                 # delivered / transient / permanent / timeout
    detail: str                  # 判定依据（含应答码与增强码）
    wait_after: float = 0.0      # 本次失败后退避等待秒数


@dataclass
class DeliveryResult:
    ok: bool
    attempts: List[AttemptRecord]
    total_time: float
    final_error: Optional[str] = None


def deliver(transport_factory: Callable[[], object], *,
            sender: str,
            recipients: Sequence[str],
            message: Union[str, bytes],
            policy: Optional[DeliveryPolicy] = None,
            clock=None,
            server_name: str = "localhost",
            rand: Optional[Callable[[], float]] = None,
            on_event: Optional[EventType] = None) -> DeliveryResult:
    """投递一封邮件。

    - 临时失败（4xx、超时、协议错误、断连）：按 BackoffPolicy 退避后重开新连接重试；
    - 永久失败（5xx，如 550/5.1.1）：立刻中止，不重试、不等待；
    - 整体超时：每轮循环检查总预算，预算耗尽即停止；单次会话资源由 SmtpSession
      在 finally 中保证释放。

    transport_factory: 每次尝试都应返回一个全新的、未做过握手的 Transport。
    rand: 退避随机源注入（便于确定性测试）。
    on_event: 回调签名 (attempt, SessionEvent)。
    """
    policy = policy or DeliveryPolicy()
    clock = clock or RealClock()
    start = clock.monotonic()
    overall_deadline = (start + policy.overall_timeout
                        if policy.overall_timeout is not None else None)
    attempts: List[AttemptRecord] = []

    def finish(ok: bool, error: Optional[str]) -> DeliveryResult:
        return DeliveryResult(ok=ok, attempts=attempts,
                              total_time=clock.monotonic() - start,
                              final_error=error)

    for attempt in range(1, policy.max_attempts + 1):
        if overall_deadline is not None:
            remaining = overall_deadline - clock.monotonic()
            if remaining <= 0:
                attempts.append(AttemptRecord(
                    attempt, Stage.ABORTED.value, "timeout",
                    "整体超时预算耗尽，停止重试"))
                return finish(False, "整体超时，投递未完成（会话资源均已释放）")
            session_timeout = min(policy.session_timeout, remaining)
        else:
            session_timeout = policy.session_timeout

        transport = None
        try:
            try:
                transport = transport_factory()
            except OSError as exc:
                raise ConnectionLost(f"建立连接失败: {exc}") from exc

            deadline = clock.monotonic() + session_timeout
            if hasattr(transport, "set_deadline"):
                transport.set_deadline(deadline)

            def forward(event: SessionEvent, _attempt=attempt) -> None:
                if on_event is not None:
                    on_event(_attempt, event)

            session = SmtpSession(
                transport, sender=sender, recipients=recipients,
                message=message, policy=policy, clock=clock,
                deadline=deadline, server_name=server_name,
                on_event=forward)
            session.run()
        except PermanentFailure as exc:
            _safe_close(transport)
            attempts.append(AttemptRecord(
                attempt, _stage_of(exc), "permanent", str(exc)))
            return finish(False, f"永久性拒绝，立刻中止且不再重试: {exc}")
        except SmtpDeliveryError as exc:
            _safe_close(transport)
            if not exc.retryable:
                attempts.append(AttemptRecord(
                    attempt, _stage_of(exc), "permanent", str(exc)))
                return finish(False, f"遇到不可重试错误，立刻中止: {exc}")
            wait = (policy.backoff.delay(attempt, rand)
                    if attempt < policy.max_attempts else 0.0)
            attempts.append(AttemptRecord(
                attempt, _stage_of(exc), "transient", str(exc), wait))
            if wait:
                clock.sleep(wait)
            continue
        else:
            attempts.append(AttemptRecord(
                attempt, Stage.DONE.value, "delivered",
                "信体被对端接收（DATA 后收到 250）"))
            return finish(True, None)

    return finish(False,
                  f"已重试 {policy.max_attempts} 次仍被临时拒绝，放弃投递")


def _safe_close(transport) -> None:
    if transport is None:
        return
    try:
        transport.close()
    except Exception:
        pass


def _stage_of(exc: SmtpDeliveryError) -> str:
    stage = getattr(exc, "stage", "unknown")
    if isinstance(stage, Stage):
        return stage.value
    if stage == "unknown":
        return Stage.CONNECT.value
    return str(stage)
