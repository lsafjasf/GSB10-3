"""采样率统计：按 (级别, 通道) 聚合实际保留率并与目标率对照。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .layered_sampler import Event, Level


@dataclass
class GroupStats:
    level: Level
    channel: str
    seen: int = 0
    kept: int = 0
    _target_sum: float = 0.0
    _sources: set = field(default_factory=set)

    def __post_init__(self) -> None:
        if not isinstance(self._sources, set):
            self._sources = set(self._sources)

    @property
    def target_rate(self) -> float:
        """该组事件的平均目标率（含强制保留请求的 1.0 混合）。"""
        return self._target_sum / self.seen if self.seen else float("nan")

    @property
    def target_source(self) -> str:
        return "+".join(sorted(self._sources)) or ""

    @property
    def actual_rate(self) -> float:
        return self.kept / self.seen if self.seen else float("nan")

    @property
    def abs_error(self) -> float:
        """绝对偏差 |实际率 - 目标率|。"""
        if not self.seen:
            return float("nan")
        return abs(self.actual_rate - self.target_rate)

    @property
    def rel_error(self) -> float:
        """相对偏差（目标率为 0 时仅在绝对偏差也为 0 时记 0）。"""
        if not self.seen:
            return float("nan")
        if self.target_rate == 0.0:
            return 0.0 if self.abs_error == 0.0 else float("inf")
        return self.abs_error / self.target_rate

    @property
    def ci95_halfwidth(self) -> float:
        """二项分布下实际率 95% 置信区间半宽（Wald）。"""
        if not self.seen:
            return float("nan")
        p = self.actual_rate
        return 1.96 * math.sqrt(p * (1 - p) / self.seen)

    @property
    def within_ci(self) -> Optional[bool]:
        """实际率是否落在目标率的 95% 置信区间内（None=目标率为边界值）。"""
        if not self.seen or self.target_rate in (0.0, 1.0):
            return None
        return abs(self.actual_rate - self.target_rate) <= self.ci95_halfwidth


class Metrics:
    """线程安全的采样统计器。

    记录每个 (级别, 通道) 的观察数、保留数和目标率（取均值）。
    target_rate 应传请求级决策实际采用的治理目标率；规则更新后请
    另建 Metrics 分段统计，避免新旧规则混在一起无法对照。
    """

    def __init__(self) -> None:
        self._groups: Dict[Tuple[Level, str], GroupStats] = {}

    def record(
        self,
        event: Event,
        kept: bool,
        target_rate: float,
        target_source: str = "",
    ) -> None:
        """target_rate 传该请求实际采用的治理目标率（而非事件单独匹配率）。"""
        key = event.group_key
        g = self._groups.get(key)
        if g is None:
            g = GroupStats(level=event.level, channel=event.channel)
            self._groups[key] = g
        g.seen += 1
        g._target_sum += target_rate
        if target_source:
            g._sources.add(target_source)
        if kept:
            g.kept += 1

    def groups(self) -> List[GroupStats]:
        return sorted(
            self._groups.values(), key=lambda g: (-int(g.level), g.channel)
        )

    def total(self) -> Tuple[int, int]:
        return (
            sum(g.seen for g in self._groups.values()),
            sum(g.kept for g in self._groups.values()),
        )

    def to_rows(self) -> List[dict]:
        seen, kept = self.total()
        rows = []
        for g in self.groups():
            rows.append(
                {
                    "level": g.level.name,
                    "channel": g.channel,
                    "seen": g.seen,
                    "kept": g.kept,
                    "actual_rate": _r(g.actual_rate),
                    "target_rate": _r(g.target_rate),
                    "abs_error": _r(g.abs_error),
                    "rel_error": _r(g.rel_error),
                    "ci95_halfwidth": _r(g.ci95_halfwidth),
                    "within_ci": g.within_ci,
                    "target_source": g.target_source,
                }
            )
        return rows

    def to_markdown(self, title: str = "") -> str:
        rows = self.to_rows()
        lines = []
        if title:
            lines.append(f"### {title}")
            lines.append("")
        seen, kept = self.total()
        overall = (kept / seen) if seen else float("nan")
        lines.append(
            f"合计: {seen} 条事件, 保留 {kept} 条, 整体实际保留率 "
            f"{_r(overall)}"
        )
        lines.append("")
        lines.append(
            "| 级别 | 通道 | 观察数 | 保留数 | 实际采样率 | 目标采样率 | 绝对偏差 | 相对偏差 | 95%CI半宽 | 区间内 | 目标来源 |"
        )
        lines.append(
            "|---|---|---:|---:|---:|---:|---:|---:|---:|:---:|---|"
        )
        for r in rows:
            lines.append(
                "| {level} | {channel} | {seen} | {kept} | {actual_rate} "
                "| {target_rate} | {abs_error} | {rel_error} "
                "| {ci95_halfwidth} | {within} | {target_source} |".format(
                    within=(
                        "-"
                        if r["within_ci"] is None
                        else ("是" if r["within_ci"] else "否")
                    ),
                    **r,
                )
            )
        return "\n".join(lines) + "\n"

    def to_csv(self) -> str:
        rows = self.to_rows()
        header = (
            "level,channel,seen,kept,actual_rate,target_rate,abs_error,"
            "rel_error,ci95_halfwidth,within_ci,target_source"
        )
        out = [header]
        for r in rows:
            out.append(
                ",".join(
                    str(r[k]) if r[k] is not None else ""
                    for k in (
                        "level",
                        "channel",
                        "seen",
                        "kept",
                        "actual_rate",
                        "target_rate",
                        "abs_error",
                        "rel_error",
                        "ci95_halfwidth",
                        "within_ci",
                        "target_source",
                    )
                )
            )
        return "\n".join(out) + "\n"


def _r(x: float, nd: int = 4) -> str:
    if x != x:  # NaN
        return ""
    if math.isinf(x):
        return "inf"
    return f"{x:.{nd}f}"
