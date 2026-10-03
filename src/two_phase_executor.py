"""两阶段批量执行器（仅依赖标准库）。

阶段 1 prepare：逐个准备；任一失败立即中止，并按逆序回退已完成准备的项。
阶段 2 commit ：逐个提交；任一失败时，尝试回退已提交/已准备但未提交的项。
                提交后无法回退的项会被记录为 unrecoverable（部分成功）。

参与者契约（Participant）：
    prepare() / commit() / rollback() 都必须幂等：
    对同一状态重复调用不得产生重复副作用（重复调用应短路为空操作）。
    执行器自身也保证同一阶段对同一参与者只调用一次；
    但幂等是参与者的硬性契约，可用 assert_participant_idempotent 验证。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Callable, List, Optional, Protocol, runtime_checkable

# ---- 单项状态常量 ---------------------------------------------------------
NOT_REACHED = "not_reached"   # 尚未执行到
OK = "ok"
FAILED = "failed"
SKIPPED = "skipped"           # 因前项失败被跳过
ROLLED_BACK = "rolled_back"   # 成功回退
UNRECOVERABLE = "unrecoverable"  # 提交后无法回退
NOT_NEEDED = "not_needed"

# ---- 整体结论常量 ---------------------------------------------------------
SUCCESS = "SUCCESS"                              # 全部提交成功
PREPARE_FAILED_ROLLED_BACK = "PREPARE_FAILED_ROLLED_BACK"  # 准备失败且干净回退
PREPARE_FAILED_ROLLBACK_FAILED = "PREPARE_FAILED_ROLLBACK_FAILED"  # 准备失败且回退也失败
COMMIT_FAILED_PARTIAL = "COMMIT_FAILED_PARTIAL"  # 提交失败，存在已提交无法回退的项
COMMIT_FAILED_ROLLED_BACK = "COMMIT_FAILED_ROLLED_BACK"  # 提交失败，尚未提交的项已干净回退
EMPTY = "SUCCESS_EMPTY"


class ParticipantError(RuntimeError):
    """参与者操作抛出的异常会被执行器包装为该类型并记录。"""


@runtime_checkable
class Participant(Protocol):
    name: str

    def prepare(self) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


@dataclass
class ItemResult:
    name: str
    prepare: str = NOT_REACHED
    commit: str = NOT_REACHED
    rollback: str = NOT_NEEDED
    error: Optional[str] = None

    @property
    def committed(self) -> bool:
        return self.commit == OK

    @property
    def dirty(self) -> bool:
        """该项最终是否处于无法保证一致性的状态。"""
        return self.commit == FAILED or self.rollback in (FAILED, UNRECOVERABLE)


@dataclass
class BatchResult:
    status: str
    items: List[ItemResult] = field(default_factory=list)
    unrecoverable: List[str] = field(default_factory=list)

    @property
    def partial_success(self) -> bool:
        """部分成功：至少一项已提交，但整体不是全成功。"""
        if self.status == SUCCESS:
            return False
        return any(item.committed for item in self.items)

    @property
    def any_failure(self) -> bool:
        return self.status not in (SUCCESS, EMPTY)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "partial_success": self.partial_success,
            "unrecoverable": list(self.unrecoverable),
            "items": [asdict(item) for item in self.items],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


class TwoPhaseBatchExecutor:
    """按顺序执行两阶段批量操作。"""

    def __init__(self, participants: List[Participant]):
        names = [p.name for p in participants]
        if len(names) != len(set(names)):
            raise ValueError(f"参与者名称必须唯一: {names}")
        self._participants = list(participants)
        self._results = {p.name: ItemResult(p.name) for p in participants}

    # -- 公共入口 -----------------------------------------------------------
    def run(self) -> BatchResult:
        if not self._participants:
            return BatchResult(status=EMPTY)

        prepared: List[Participant] = []
        for participant in self._participants:
            result = self._results[participant.name]
            try:
                participant.prepare()
                result.prepare = OK
                prepared.append(participant)
            except Exception as exc:  # noqa: BLE001 - 需要捕获参与者的任意失败
                result.prepare = FAILED
                result.error = f"prepare: {type(exc).__name__}: {exc}"
                # 后续项一律跳过，不再调用其 prepare
                for later in self._participants[self._participants.index(participant) + 1:]:
                    later_result = self._results[later.name]
                    later_result.prepare = SKIPPED
                    later_result.commit = SKIPPED
                return self._abort_after_prepare_failure(prepared)

        return self._commit_phase(prepared)

    # -- 提交阶段 -----------------------------------------------------------
    def _commit_phase(self, prepared: List[Participant]) -> BatchResult:
        committed: List[Participant] = []
        for participant in prepared:
            result = self._results[participant.name]
            try:
                participant.commit()
                result.commit = OK
                committed.append(participant)
            except Exception as exc:  # noqa: BLE001
                result.commit = FAILED
                result.error = f"commit: {type(exc).__name__}: {exc}"
                for later in prepared[prepared.index(participant) + 1:]:
                    later_result = self._results[later.name]
                    later_result.commit = SKIPPED  # 已 prepare 成功，未 commit
                return self._abort_after_commit_failure(committed, participant, prepared)

        for result in self._results.values():
            result.rollback = NOT_NEEDED
        return BatchResult(status=SUCCESS, items=self._ordered_items())

    # -- 准备失败后的回退（逆序） -------------------------------------------
    def _abort_after_prepare_failure(
        self, prepared: List[Participant]
    ) -> BatchResult:
        rollback_failed = self._rollback_all(reversed(prepared))
        status = (
            PREPARE_FAILED_ROLLBACK_FAILED if rollback_failed
            else PREPARE_FAILED_ROLLED_BACK
        )
        return BatchResult(
            status=status,
            items=self._ordered_items(),
            unrecoverable=[p.name for p in rollback_failed],
        )

    # -- 提交失败后的回退 ---------------------------------------------------
    def _abort_after_commit_failure(
        self,
        committed: List[Participant],
        failed: Participant,
        prepared: List[Participant],
    ) -> BatchResult:
        unrecoverable: List[Participant] = []

        # 已提交的项：尝试回退；通常提交不可逆，回退会失败 -> 记录为无法回退。
        for participant in reversed(committed):
            result = self._results[participant.name]
            try:
                participant.rollback()
                result.rollback = ROLLED_BACK
            except Exception as exc:  # noqa: BLE001
                result.rollback = UNRECOVERABLE
                result.error = (
                    (result.error + " | " if result.error else "")
                    + f"rollback-after-commit: {type(exc).__name__}: {exc}"
                )
                unrecoverable.append(participant)

        # 提交失败的那一项本身：也尝试回退其准备效果。
        failed_result = self._results[failed.name]
        if failed_result.rollback == NOT_NEEDED:
            try:
                failed.rollback()
                failed_result.rollback = ROLLED_BACK
            except Exception as exc:  # noqa: BLE001
                failed_result.rollback = FAILED
                failed_result.error = (
                    (failed_result.error + " | " if failed_result.error else "")
                    + f"rollback: {type(exc).__name__}: {exc}"
                )

        # 已准备但尚未提交的项：正常逆序回退。
        committed_names = {p.name for p in committed}
        pending = [
            p for p in prepared
            if p.name not in committed_names and p.name != failed.name
        ]
        rollback_failed = self._rollback_all(reversed(pending))

        if unrecoverable or rollback_failed:
            status = COMMIT_FAILED_PARTIAL
        else:
            status = COMMIT_FAILED_ROLLED_BACK
        return BatchResult(
            status=status,
            items=self._ordered_items(),
            unrecoverable=[p.name for p in unrecoverable]
            + [p.name for p in rollback_failed],
        )

    # -- 工具 ---------------------------------------------------------------
    def _rollback_all(
        self, participants: List[Participant]
    ) -> List[Participant]:
        """逐项回退；单项失败不影响其余项继续回退，返回回退失败的项。"""
        failed: List[Participant] = []
        for participant in participants:
            result = self._results[participant.name]
            if result.rollback == ROLLED_BACK:
                continue  # 幂等保护：已回退的不再调用
            try:
                participant.rollback()
                result.rollback = ROLLED_BACK
            except Exception as exc:  # noqa: BLE001
                result.rollback = FAILED
                result.error = (
                    (result.error + " | " if result.error else "")
                    + f"rollback: {type(exc).__name__}: {exc}"
                )
                failed.append(participant)
        return failed

    def _ordered_items(self) -> List[ItemResult]:
        return [self._results[p.name] for p in self._participants]


# ---- 幂等断言 -------------------------------------------------------------
def assert_participant_idempotent(factory: Callable[[], Participant]) -> None:
    """参与者幂等契约断言。

    factory 每次返回一个全新参与者。通过“副作用计数器 + 状态重复调用”
    验证：对同一参与者在同一阶段重复调用 prepare/commit/rollback，
    副作用计数不得增加，且不得抛异常。
    """

    # 场景 1：prepare 重复调用 -> 只产生一次准备副作用
    p = factory()
    p.prepare()
    p.prepare()
    p.prepare()
    counts = getattr(p, "effect_counts", None)
    if counts is None:
        raise AssertionError(
            f"{p.name}: 幂等断言需要参与者暴露 effect_counts 字典"
        )
    if counts.get("prepare", 0) != 1:
        raise AssertionError(
            f"{p.name}: prepare 重复调用产生 {counts.get('prepare')} 次副作用，期望 1"
        )

    # 场景 2：commit 后重复 commit -> 只提交一次，且不再产生 prepare 副作用
    p.commit()
    p.commit()
    if counts.get("commit", 0) != 1:
        raise AssertionError(
            f"{p.name}: commit 重复调用产生 {counts.get('commit')} 次副作用，期望 1"
        )

    # 场景 3：rollback 重复调用 -> 只回退一次（未提交路径）
    p2 = factory()
    p2.prepare()
    p2.rollback()
    p2.rollback()
    c2 = p2.effect_counts
    if c2.get("rollback", 0) != 1:
        raise AssertionError(
            f"{p2.name}: rollback 重复调用产生 {c2.get('rollback')} 次副作用，期望 1"
        )
    # 回退后再 prepare/commit 也不应复活副作用
    p2.rollback()
    if c2.get("rollback", 0) != 1:
        raise AssertionError(f"{p2.name}: rollback 未保持幂等")


# ---- 演示入口 -------------------------------------------------------------
if __name__ == "__main__":
    from demo_participants import build_demo  # type: ignore

    for title, participants in build_demo():
        print(f"=== {title} ===")
        result = TwoPhaseBatchExecutor(participants).run()
        print(result.to_json())
        print(f"结论: {result.status} | 部分成功: {result.partial_success}")
        print()
