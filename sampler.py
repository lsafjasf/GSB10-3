"""分层日志采样库（仅依赖 Python 标准库）。

设计要点
--------
1. 按 *级别(level)* 与 *通道(channel)* 配置采样率，规则可随时整体热更新。
2. 匹配优先级：具体度高者优先（同时指定 level+channel > 只指定其一 > 全通配），
   具体度相同时 ``priority`` 数值大者优先，再相同则按规则的添加顺序。
3. 采样决策对 ``(请求ID, 命中规则)`` 是确定性的：
   ``bucket = md5(规则键 + 请求ID) 的前 8 字节``，``bucket < rate`` 即保留。
   因此同一请求内命中同一规则类别的所有日志命运一致（全留或全丢），
   且结果跨进程稳定（不使用受 PYTHONHASHSEED 影响的内置 hash）。
4. 每条规则分别记录 命中数/保留数，可计算实际采样率相对目标采样率的偏差。

一致性语义说明
~~~~~~~~~~~~~~
不同级别/通道可能有不同目标采样率（例如 ERROR=1.0、DEBUG=0.0），
所以"全留/全丢"的粒度是 **同一请求 + 同一命中规则类别**：
同一请求里同级别同通道的日志要么全部保留，要么全部丢弃。
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

_2_POW_64 = 1 << 64


@dataclass(frozen=True)
class SamplingRule:
    """一条采样规则。

    rate:    目标采样率，[0.0, 1.0]；0 表示全丢弃，1 表示全保留。
    level:   匹配的日志级别，None 表示任意级别（通配）。
    channel: 匹配的日志通道，None 表示任意通道（通配）。
    priority: 具体度相同时的附加优先级，数值越大越优先。
    """

    rate: float
    level: Optional[str] = None
    channel: Optional[str] = None
    priority: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.rate, (int, float)) or isinstance(self.rate, bool):
            raise TypeError("rate 必须是数字")
        if not 0.0 <= float(self.rate) <= 1.0:
            raise ValueError(f"rate 必须落在 [0.0, 1.0]，实际为 {self.rate!r}")
        object.__setattr__(self, "rate", float(self.rate))

    @property
    def specificity(self) -> int:
        """具体度：命中条件中非通配字段的数量（0~2）。"""
        return (self.level is not None) + (self.channel is not None)

    def matches(self, level: Optional[str], channel: Optional[str]) -> bool:
        if self.level is not None and self.level != level:
            return False
        if self.channel is not None and self.channel != channel:
            return False
        return True

    @property
    def key(self) -> str:
        """规则类别标识，用于哈希分桶与统计聚合。"""
        return f"{self.level or '*'}:{self.channel or '*'}"


@dataclass
class RuleStat:
    target_rate: float
    total: int = 0  # 命中（产生决策）的日志条数
    kept: int = 0   # 实际保留的日志条数

    @property
    def actual_rate(self) -> float:
        return self.kept / self.total if self.total else 0.0

    @property
    def deviation(self) -> float:
        """实际采样率 - 目标采样率。"""
        return (self.actual_rate - self.target_rate) if self.total else 0.0

    @property
    def abs_deviation(self) -> float:
        return abs(self.deviation)


class Sampler:
    """分层采样器，线程安全；规则可热更新。"""

    def __init__(
        self,
        rules: Optional[List[SamplingRule]] = None,
        default_rate: float = 1.0,
    ) -> None:
        if not 0.0 <= float(default_rate) <= 1.0:
            raise ValueError("default_rate 必须落在 [0.0, 1.0]")
        self._lock = threading.Lock()
        self._default_rate = float(default_rate)
        self._rules: List[SamplingRule] = []
        # key -> RuleStat；规则更新后统计重置（新旧规则口径不同，不可混算）
        self._stats: Dict[str, RuleStat] = {}
        if rules:
            self.update_rules(rules)
        else:
            self._reset_stats(self._rules)

    # ---------- 规则管理 ----------

    def update_rules(self, rules: List[SamplingRule]) -> None:
        """整体替换规则并重新确定优先级；统计计数随之清零。"""
        # stable 排序保留同一 (specificity, priority) 下的原添加顺序
        ordered = sorted(rules, key=lambda r: (-r.specificity, -r.priority))
        with self._lock:
            self._rules = ordered
            self._reset_stats(ordered)

    def _reset_stats(self, rules: List[SamplingRule]) -> None:
        stats: Dict[str, RuleStat] = {
            r.key: RuleStat(r.rate) for r in rules
        }
        stats["__default__"] = RuleStat(self._default_rate)
        self._stats = stats

    def describe_priority(self) -> List[str]:
        """返回当前规则的匹配优先级说明，便于排查与校验。"""
        with self._lock:
            lines = [
                f"#{i + 1} key={r.key} "
                f"rate={r.rate} specificity={r.specificity} priority={r.priority}"
                for i, r in enumerate(self._rules)
            ]
        lines.append(
            f"#fallback default_rate={self._default_rate}（无任何规则命中时使用）"
        )
        return lines

    def matched_rule(
        self, level: Optional[str], channel: Optional[str]
    ) -> Tuple[str, float]:
        """返回 (规则键, 目标采样率)；按优先级取第一条命中的规则。"""
        with self._lock:
            for rule in self._rules:
                if rule.matches(level, channel):
                    return rule.key, float(rule.rate)
            return "__default__", self._default_rate

    # ---------- 采样决策 ----------

    def should_keep(self, level: Optional[str], channel: Optional[str], request_id: object) -> bool:
        """判断一条日志是否保留。

        决策只依赖 request_id 与命中规则，同一输入永远得到同一结果，
        从而保证请求级一致性。request_id 会用 str() 归一化，字符串/数字均可。
        """
        key, rate = self.matched_rule(level, channel)

        if rate <= 0.0:
            decision = False
        elif rate >= 1.0:
            decision = True
        else:
            digest = hashlib.md5(f"{key}|{request_id}".encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:8], "big") / _2_POW_64
            decision = bucket < rate

        with self._lock:
            stat = self._stats[key]
            stat.total += 1
            if decision:
                stat.kept += 1
        return decision

    # ---------- 统计与偏差 ----------

    def report(self) -> List[dict]:
        """返回每条规则（含默认兜底）的目标/实际采样率与偏差对照数据。"""
        with self._lock:
            return [
                {
                    "rule_key": key,
                    "target_rate": stat.target_rate,
                    "total": stat.total,
                    "kept": stat.kept,
                    "actual_rate": stat.actual_rate,
                    "deviation": stat.deviation,
                    "abs_deviation": stat.abs_deviation,
                }
                for key, stat in self._stats.items()
                if stat.total > 0
            ]

    def max_abs_deviation(self) -> float:
        return max((row["abs_deviation"] for row in self.report()), default=0.0)
