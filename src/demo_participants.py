"""演示/测试用的幂等参与者实现。

SimulatedParticipant 用内存状态机模拟真实资源：
    idle -> prepared -> committed
    prepared -> rolled_back -> idle
所有操作幂等：对当前状态不适用的重复调用直接短路，不产生副作用。
effect_counts 记录真实副作用次数，供幂等断言使用。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple


class SimulatedParticipant:
    def __init__(
        self,
        name: str,
        fail_prepare: bool = False,
        fail_commit: bool = False,
        fail_rollback: bool = False,
        committed_rollback_impossible: bool = True,
    ):
        self.name = name
        self.fail_prepare = fail_prepare
        self.fail_commit = fail_commit
        self.fail_rollback = fail_rollback
        # 真实场景：提交通常不可逆，提交后回退默认抛错
        self.committed_rollback_impossible = committed_rollback_impossible
        self.state = "idle"  # idle | prepared | committed
        self.effect_counts: Dict[str, int] = {
            "prepare": 0,
            "commit": 0,
            "rollback": 0,
        }

    # -- 幂等操作 -----------------------------------------------------------
    def prepare(self) -> None:
        if self.state != "idle":
            return  # 幂等短路：已准备/已提交时重复 prepare 无副作用
        if self.fail_prepare:
            raise RuntimeError(f"{self.name}: prepare 注入失败")
        self.state = "prepared"
        self.effect_counts["prepare"] += 1

    def commit(self) -> None:
        if self.state == "committed":
            return  # 幂等短路
        if self.state != "prepared":
            raise RuntimeError(f"{self.name}: 未 prepare 不能 commit")
        if self.fail_commit:
            raise RuntimeError(f"{self.name}: commit 注入失败")
        self.state = "committed"
        self.effect_counts["commit"] += 1

    def rollback(self) -> None:
        if self.state == "idle":
            return  # 幂等短路：无事可退
        if self.state == "committed" and self.committed_rollback_impossible:
            raise RuntimeError(f"{self.name}: 已提交，无法回退")
        if self.fail_rollback:
            raise RuntimeError(f"{self.name}: rollback 注入失败")
        self.state = "idle"
        self.effect_counts["rollback"] += 1


def build_demo() -> List[Tuple[str, List[SimulatedParticipant]]]:
    """四种典型场景，供 __main__ 演示。"""
    return [
        ("全部成功", [SimulatedParticipant(f"svc-{i}") for i in range(3)]),
        (
            "准备失败（第 2 项）",
            [
                SimulatedParticipant("svc-0"),
                SimulatedParticipant("svc-1", fail_prepare=True),
                SimulatedParticipant("svc-2"),
            ],
        ),
        (
            "提交失败（第 1 项，已提交项无法回退）",
            [
                SimulatedParticipant("svc-0"),
                SimulatedParticipant("svc-1", fail_commit=True),
                SimulatedParticipant("svc-2"),
            ],
        ),
        (
            "回退失败（准备失败 + 回退也失败）",
            [
                SimulatedParticipant("svc-0", fail_rollback=True),
                SimulatedParticipant("svc-1", fail_prepare=True),
            ],
        ),
    ]
