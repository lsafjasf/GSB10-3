"""告警抑制与聚合库（仅依赖 Python 标准库）。

核心概念
--------
- Alert:               单条原始告警。
- SuppressionRule:     抑制规则，只压制被字段显式匹配的告警；可设置生效时间窗。
- SuppressionRecord:   抑制记录，记录"哪条告警被哪条规则、因为什么、在何时压制"。
- AggregatedAlert:     聚合告警，按 (来源, 类型) 在滑动时间窗口内合并，保留计数与成员。
- AlertAggregator:     纯聚合器。
- AlertEngine:         先抑制、后聚合的完整引擎；抑制与未匹配完全隔离。

时间可注入：clock 是一个无参可调用对象，返回 UNIX 秒（float）。默认 time.time。
"""

from __future__ import annotations

import fnmatch
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Dict, List, Optional, Tuple


class Severity(IntEnum):
    INFO = 10
    WARNING = 20
    ERROR = 30
    CRITICAL = 40


@dataclass(frozen=True)
class Alert:
    """单条原始告警。"""

    alert_id: str
    source: str                 # 来源，如 "host-7"
    alert_type: str             # 类型，如 "cpu_high"
    severity: Severity
    message: str
    timestamp: float
    labels: Tuple[Tuple[str, str], ...] = ()  # 不可变，便于追溯与去重

    @classmethod
    def create(
        cls,
        alert_id: str,
        source: str,
        alert_type: str,
        severity: Severity,
        message: str,
        timestamp: float,
        labels: Optional[Dict[str, str]] = None,
    ) -> "Alert":
        return cls(
            alert_id=alert_id,
            source=source,
            alert_type=alert_type,
            severity=severity,
            message=message,
            timestamp=timestamp,
            labels=tuple(sorted((labels or {}).items())),
        )

    def label(self, key: str) -> Optional[str]:
        for k, v in self.labels:
            if k == key:
                return v
        return None


@dataclass(frozen=True)
class SuppressionRule:
    """抑制规则。

    匹配原则：每个已设置的字段都必须命中（AND）；未设置的字段表示"不限"。
    至少需要设置一个匹配字段，避免一条规则意外压制全部告警。
    source / alert_type 支持 fnmatch 通配符（* ? [seq]）。
    labels 中要求的每个键值都必须出现在告警上。
    starts_at / expires_at 为可选的生效时间窗（左闭右开）。
    """

    rule_id: str
    reason: str
    source: Optional[str] = None
    alert_type: Optional[str] = None
    min_severity: Optional[Severity] = None   # 仅压制该级别及以下
    labels: Tuple[Tuple[str, str], ...] = ()
    starts_at: Optional[float] = None
    expires_at: Optional[float] = None

    def __post_init__(self) -> None:
        criteria = (
            self.source,
            self.alert_type,
            self.min_severity,
            self.labels,
        )
        if not any(criteria):
            raise ValueError(
                f"规则 {self.rule_id!r} 必须至少设置一个匹配字段，"
                "禁止创建覆盖一切的抑制规则"
            )

    @classmethod
    def create(
        cls,
        rule_id: str,
        reason: str,
        source: Optional[str] = None,
        alert_type: Optional[str] = None,
        min_severity: Optional[Severity] = None,
        labels: Optional[Dict[str, str]] = None,
        starts_at: Optional[float] = None,
        expires_at: Optional[float] = None,
    ) -> "SuppressionRule":
        return cls(
            rule_id=rule_id,
            reason=reason,
            source=source,
            alert_type=alert_type,
            min_severity=min_severity,
            labels=tuple(sorted((labels or {}).items())),
            starts_at=starts_at,
            expires_at=expires_at,
        )

    def matches(self, alert: Alert, now: float) -> bool:
        """规则是否在 now 时刻命中该告警。任何一项不满足都不压制。"""
        if self.starts_at is not None and now < self.starts_at:
            return False
        if self.expires_at is not None and now >= self.expires_at:
            return False
        if self.source is not None and not fnmatch.fnmatchcase(
            alert.source, self.source
        ):
            return False
        if self.alert_type is not None and not fnmatch.fnmatchcase(
            alert.alert_type, self.alert_type
        ):
            return False
        if self.min_severity is not None and alert.severity > self.min_severity:
            return False
        if self.labels:
            present = dict(alert.labels)
            for key, value in self.labels:
                if present.get(key) != value:
                    return False
        return True


@dataclass(frozen=True)
class SuppressionRecord:
    """一条抑制记录，保证被压制的告警可追溯。"""

    alert: Alert
    rule: SuppressionRule
    suppressed_at: float

    def to_dict(self) -> dict:
        return {
            "alert_id": self.alert.alert_id,
            "source": self.alert.source,
            "alert_type": self.alert.alert_type,
            "severity": self.alert.severity.name,
            "rule_id": self.rule.rule_id,
            "reason": self.rule.reason,
            "suppressed_at": self.suppressed_at,
        }


@dataclass
class AggregatedAlert:
    """窗口内同 (来源, 类型) 的告警合并结果。"""

    source: str
    alert_type: str
    count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    max_severity: Severity = Severity.INFO
    member_ids: List[str] = field(default_factory=list)

    @property
    def key(self) -> Tuple[str, str]:
        return (self.source, self.alert_type)


class AlertAggregator:
    """按 (来源, 类型) 做滑动窗口聚合。

    窗口语义：同一 key 下，若新告警距该组最早一条（first_seen）超过
    window_seconds，则先把旧组冲刷为一条聚合输出，再开新组。
    window_seconds <= 0 表示不聚合，每条单独成组。
    """

    def __init__(self, window_seconds: float) -> None:
        if window_seconds < 0:
            raise ValueError("window_seconds 不能为负")
        self.window_seconds = window_seconds
        self._groups: "OrderedDict[Tuple[str, str], AggregatedAlert]" = OrderedDict()

    def add(self, alert: Alert) -> List[AggregatedAlert]:
        """加入一条告警，返回本次加入导致冲刷出的聚合（可能为空）。"""
        flushed: List[AggregatedAlert] = []
        key = (alert.source, alert.alert_type)
        group = self._groups.get(key)

        if (
            group is not None
            and self.window_seconds > 0
            and alert.timestamp - group.first_seen > self.window_seconds
        ):
            flushed.append(self._groups.pop(key))
            group = None

        if group is None:
            group = AggregatedAlert(
                source=alert.source,
                alert_type=alert.alert_type,
                first_seen=alert.timestamp,
            )
            self._groups[key] = group

        group.count += 1
        group.last_seen = alert.timestamp
        group.member_ids.append(alert.alert_id)
        if alert.severity > group.max_severity:
            group.max_severity = alert.severity
        return flushed

    def flush(self) -> List[AggregatedAlert]:
        """冲刷全部未决分组（窗口结束 / 引擎收数时调用）。"""
        ready = list(self._groups.values())
        self._groups.clear()
        return ready

    def pending(self) -> List[AggregatedAlert]:
        """查看未决分组但不冲刷。"""
        return list(self._groups.values())


@dataclass
class IngestResult:
    """单条告警处理结果。被抑制时 aggregated 为 None。"""

    alert: Alert
    suppressed: bool
    record: Optional[SuppressionRecord]
    flushed: List[AggregatedAlert]


class AlertEngine:
    """先抑制、后聚合的告警引擎。

    - 未命中任何规则的告警：进入聚合器，并完整保存在 unaffected 清单中。
    - 命中规则的告警：不进入聚合器，仅记录在 suppression_records 中。
    两条通道互不影响。
    """

    def __init__(
        self,
        rules: Optional[List[SuppressionRule]] = None,
        window_seconds: float = 60.0,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.clock = clock or time.time
        self.rules: List[SuppressionRule] = list(rules or [])
        self.aggregator = AlertAggregator(window_seconds)
        self.suppression_records: List[SuppressionRecord] = []
        self.unaffected: List[Alert] = []

    def add_rule(self, rule: SuppressionRule) -> None:
        self.rules.append(rule)

    def _find_rule(self, alert: Alert, now: float) -> Optional[SuppressionRule]:
        """返回命中的第一条规则（规则顺序即优先级），无命中返回 None。"""
        for rule in self.rules:
            if rule.matches(alert, now):
                return rule
        return None

    def ingest(self, alert: Alert, timestamp: Optional[float] = None) -> IngestResult:
        now = timestamp if timestamp is not None else self.clock()
        if alert.timestamp == 0:
            object.__setattr__(alert, "timestamp", now)

        rule = self._find_rule(alert, now)
        if rule is not None:
            record = SuppressionRecord(
                alert=alert, rule=rule, suppressed_at=now
            )
            self.suppression_records.append(record)
            return IngestResult(
                alert=alert, suppressed=True, record=record, flushed=[]
            )

        self.unaffected.append(alert)
        flushed = self.aggregator.add(alert)
        return IngestResult(
            alert=alert, suppressed=False, record=None, flushed=flushed
        )

    def close_window(self) -> List[AggregatedAlert]:
        """结束当前聚合窗口，返回全部聚合告警。"""
        return self.aggregator.flush()

    def suppressed_alert_ids(self) -> List[str]:
        return [r.alert.alert_id for r in self.suppression_records]

    def unaffected_alert_ids(self) -> List[str]:
        return [a.alert_id for a in self.unaffected]
