"""两阶段批量执行器（准备/提交 + 回退），仅依赖标准库。

语义：
- 准备阶段任一失败：立刻中止，按逆序回退已准备的项；回退失败被记录。
- 提交阶段任一失败：已提交的项无法回退，显式标记为 unrecoverable（部分成功）；
  尚未提交的项按逆序回退，回退失败被记录。
- 幂等：同一参与者对象的 prepare/commit/rollback 各自最多生效一次，
  重复调用为无操作；执行器对同一批参与者重复 execute 不产生额外副作用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Callable, List, Optional


class PrepareError(Exception):
    pass


class CommitError(Exception):
    pass


class RollbackError(Exception):
    pass


class OverallStatus(str, Enum):
    SUCCESS = "SUCCESS"                              # 全部成功
    ABORTED = "ABORTED"                              # 准备失败，已完整回退
    ABORTED_ROLLBACK_FAILED = "ABORTED_ROLLBACK_FAILED"  # 准备失败且回退本身失败
    PARTIAL_COMMIT = "PARTIAL_COMMIT"                # 提交失败：部分已提交无法回退（部分成功）


@dataclass
class ItemResult:
    name: str
    prepare: str = "skipped"        # ok | failed | skipped
    commit: str = "skipped"         # ok | failed | skipped
    rollback: str = "not_needed"    # ok | failed | not_needed | impossible
    unrecoverable: bool = False     # 已提交、无法回退
    detail: str = ""


@dataclass
class BatchResult:
    status: OverallStatus
    items: List[ItemResult] = field(default_factory=list)
    partial_success: bool = False
    summary: str = ""

    def to_json(self) -> str:
        data = asdict(self)
        data["status"] = self.status.value
        return json.dumps(data, ensure_ascii=False, indent=2)


class IdempotentParticipant:
    """幂等参与者基类：内部状态机保证 prepare/commit/rollback 重复调用无副作用。

    子类覆盖 _do_prepare/_do_commit/_do_rollback 实现真实副作用。
    """

    def __init__(self, name: str):
        self.name = name
        self._state = "init"  # init -> prepared -> committed；prepared -> rolled_back
        self.effect_log: List[str] = []  # 真实副作用只在这里追加，用于幂等断言

    def prepare(self) -> None:
        if self._state != "init":
            return  # 幂等：重复准备不产生副作用
        self._do_prepare()
        self._state = "prepared"

    def commit(self) -> None:
        if self._state == "committed":
            return  # 幂等：重复提交不产生副作用
        if self._state != "prepared":
            raise CommitError(f"{self.name}: commit 前必须先 prepare 成功")
        self._do_commit()
        self._state = "committed"

    def rollback(self) -> None:
        if self._state != "prepared":
            return  # 幂等：无需回退或已回退
        self._do_rollback()
        self._state = "rolled_back"

    def _do_prepare(self) -> None:
        self.effect_log.append(f"prepare:{self.name}")

    def _do_commit(self) -> None:
        self.effect_log.append(f"commit:{self.name}")

    def _do_rollback(self) -> None:
        self.effect_log.append(f"rollback:{self.name}")

    def assert_idempotent(self) -> None:
        """幂等断言：重复调用 prepare/commit/rollback 不得新增副作用。"""
        for op in (self.prepare, self.commit, self.rollback):
            op()  # 首次调用可能是合法状态迁移
            before = list(self.effect_log)
            op()
            op()
            assert self.effect_log == before, (
                f"{self.name} 幂等性被破坏: {before} -> {self.effect_log}"
            )


def _rollback(prepared: List[IdempotentParticipant],
              items: List[ItemResult]) -> bool:
    """逆序回退，返回是否存在回退失败。"""
    failed = False
    by_name = {it.name: it for it in items}
    for p in reversed(prepared):
        it = by_name[p.name]
        try:
            p.rollback()
            it.rollback = "ok"
        except Exception as exc:  # 回退本身也可能失败
            it.rollback = "failed"
            it.detail += f" rollback_error={exc!r};"
            failed = True
    return failed


class TwoPhaseBatchExecutor:
    def execute(self, participants: List[IdempotentParticipant]) -> BatchResult:
        # 边界：按身份去重，同一对象重复出现只执行一次
        seen, unique = set(), []
        for p in participants:
            if id(p) not in seen:
                seen.add(id(p))
                unique.append(p)

        items = [ItemResult(name=p.name) for p in unique]
        prepared: List[IdempotentParticipant] = []

        # ---- 阶段一：准备 ----
        for p, it in zip(unique, items):
            try:
                p.prepare()
                it.prepare = "ok"
                prepared.append(p)
            except Exception as exc:
                it.prepare = "failed"
                it.detail += f" prepare_error={exc!r};"
                rollback_failed = _rollback(prepared, items)
                status = (OverallStatus.ABORTED_ROLLBACK_FAILED if rollback_failed
                          else OverallStatus.ABORTED)
                return BatchResult(
                    status=status, items=items, partial_success=False,
                    summary=f"准备阶段在 {p.name} 处失败，已中止；"
                            f"回退{'存在失败' if rollback_failed else '完成'}。")

        # ---- 阶段二：提交 ----
        committed: List[IdempotentParticipant] = []
        for p, it in zip(unique, items):
            try:
                p.commit()
                it.commit = "ok"
                committed.append(p)
            except Exception as exc:
                it.commit = "failed"
                it.detail += f" commit_error={exc!r};"
                # 已提交的项无法回退 -> 显式标记
                for c in committed:
                    cit = next(x for x in items if x.name == c.name)
                    cit.unrecoverable = True
                    cit.rollback = "impossible"
                # 尚未提交的已准备项尝试回退
                remaining = [x for x in prepared if x not in committed and x is not p]
                rollback_failed = _rollback(remaining, items)
                n_unrec = len(committed)
                return BatchResult(
                    status=OverallStatus.PARTIAL_COMMIT, items=items,
                    partial_success=True,
                    summary=f"提交阶段在 {p.name} 处失败：{n_unrec} 项已提交且无法回退"
                            f"（部分成功）；其余已准备项回退"
                            f"{'存在失败' if rollback_failed else '完成'}。")

        return BatchResult(status=OverallStatus.SUCCESS, items=items,
                           partial_success=False,
                           summary=f"全部 {len(items)} 项准备并提交成功。")


# ---------------- 演示：四种情形 ----------------

class FlakyParticipant(IdempotentParticipant):
    """可注入失败的参与者，用于演示与测试。"""

    def __init__(self, name: str, fail_prepare=False, fail_commit=False,
                 fail_rollback=False):
        super().__init__(name)
        self._fail_prepare = fail_prepare
        self._fail_commit = fail_commit
        self._fail_rollback = fail_rollback

    def _do_prepare(self):
        if self._fail_prepare:
            raise PrepareError(f"{self.name} 准备失败")
        super()._do_prepare()

    def _do_commit(self):
        if self._fail_commit:
            raise CommitError(f"{self.name} 提交失败")
        super()._do_commit()

    def _do_rollback(self):
        if self._fail_rollback:
            raise RollbackError(f"{self.name} 回退失败")
        super()._do_rollback()


def _run(title, participants):
    print(f"===== {title} =====")
    result = TwoPhaseBatchExecutor().execute(participants)
    print(result.to_json())
    print()
    return result


if __name__ == "__main__":
    _run("场景1: 全部成功",
         [FlakyParticipant("A"), FlakyParticipant("B"), FlakyParticipant("C")])
    _run("场景2: 准备失败（B），回退 A",
         [FlakyParticipant("A"), FlakyParticipant("B", fail_prepare=True),
          FlakyParticipant("C")])
    _run("场景3: 提交失败（C），A/B 已提交无法回退 -> 部分成功",
         [FlakyParticipant("A"), FlakyParticipant("B"),
          FlakyParticipant("C", fail_commit=True)])
    _run("场景4: 准备失败且回退失败（A 回退失败）",
         [FlakyParticipant("A", fail_rollback=True),
          FlakyParticipant("B", fail_prepare=True)])
