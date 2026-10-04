"""告警抑制与聚合库（仅标准库，时间可注入）。

设计要点：
- 抑制规则只支持 (source, alert_type) 精确匹配 + 生效时间窗，不支持通配符，
  因此规则只能压制被明确覆盖的告警，不会误伤未匹配告警。
- 被抑制的告警逐条生成 SuppressionRecord，包含规则 id 与原始告警快照，可追溯。
- 聚合按 (source, alert_type) 分组，窗口锚定在该组第一条告警的首次出现时间，
  采用半开区间 [window_start, window_start + window_seconds)，组内告警合并为一条并保留计数。
- 时间通过 Clock 注入（clock() -> epoch 秒，float），测试与演示使用 ManualClock，
  生产可注入 time.time。
"""

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

Clock = Callable[[], float]

SEVERITY_ORDER = ("info", "warning", "critical")


def _severity_rank(severity):
    try:
        return SEVERITY_ORDER.index(severity)
    except ValueError:
        return -1


@dataclass(frozen=True)
class Alert:
    """单条告警。timestamp 为 epoch 秒（float）。"""

    alert_id: str
    source: str
    alert_type: str
    severity: str
    message: str
    timestamp: float


@dataclass(frozen=True)
class SuppressionRule:
    """抑制规则：仅精确匹配 (source, alert_type)，且在 [starts_at, ends_at) 内生效。

    starts_at / ends_at 为 None 表示该方向不限。
    """

    rule_id: str
    source: str
    alert_type: str
    starts_at: Optional[float] = None
    ends_at: Optional[float] = None
    reason: str = ""

    def is_active(self, now):
        if self.starts_at is not None and now < self.starts_at:
            return False
        if self.ends_at is not None and now >= self.ends_at:
            return False
        return True

    def covers(self, alert):
        return self.source == alert.source and self.alert_type == alert.alert_type


@dataclass(frozen=True)
class SuppressionRecord:
    """抑制记录：被压制告警的完整快照 + 命中的规则 + 判定时间。"""

    rule_id: str
    alert: Alert
    suppressed_at: float
    reason: str


class SuppressionEngine:
    """按规则逐条判定；多条规则命中时取先添加的一条（first-match-wins）。"""

    def __init__(self, rules=(), clock=None):
        self._rules = list(rules)
        self._clock = clock if clock is not None else _default_clock
        self._records = []

    @property
    def rules(self):
        return list(self._rules)

    def add_rule(self, rule):
        self._rules.append(rule)

    def evaluate(self, alert):
        """返回命中的 SuppressionRule；未命中返回 None。"""
        now = self._clock()
        for rule in self._rules:
            if rule.covers(alert) and rule.is_active(now):
                return rule
        return None

    def process(self, alert):
        """命中则记录并返回 True（被抑制），否则返回 False。"""
        rule = self.evaluate(alert)
        if rule is None:
            return False
        self._records.append(
            SuppressionRecord(
                rule_id=rule.rule_id,
                alert=alert,
                suppressed_at=self._clock(),
                reason=rule.reason,
            )
        )
        return True

    @property
    def records(self):
        return list(self._records)


@dataclass(frozen=True)
class AlertOccurrence:
    """未受影响告警清单的条目（原始告警 + 所属聚合组）。"""

    alert: Alert
    group_key: Tuple[str, str]


@dataclass
class AggregatedAlert:
    """聚合结果：一个窗口内同 (source, alert_type) 的告警合并为一条。"""

    source: str
    alert_type: str
    count: int
    first_seen: float
    last_seen: float
    max_severity: str
    alert_ids: List[str] = field(default_factory=list)
    sample_messages: List[str] = field(default_factory=list)
    closed: bool = False

    def to_dict(self):
        return {
            "source": self.source,
            "alert_type": self.alert_type,
            "count": self.count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "max_severity": self.max_severity,
            "alert_ids": list(self.alert_ids),
            "sample_messages": list(self.sample_messages),
            "closed": self.closed,
        }


class _Window:
    __slots__ = ("start", "count", "last_seen", "max_severity", "alert_ids", "sample_messages")

    def __init__(self, start):
        self.start = start
        self.count = 0
        self.last_seen = start
        self.max_severity = ""
        self.alert_ids = []
        self.sample_messages = []

    def add(self, alert):
        self.count += 1
        self.last_seen = max(self.last_seen, alert.timestamp)
        if _severity_rank(alert.severity) > _severity_rank(self.max_severity):
            self.max_severity = alert.severity
        self.alert_ids.append(alert.alert_id)
        if len(self.sample_messages) < 3 and alert.message not in self.sample_messages:
            self.sample_messages.append(alert.message)

    def to_aggregated(self, key, closed):
        return AggregatedAlert(
            source=key[0],
            alert_type=key[1],
            count=self.count,
            first_seen=self.start,
            last_seen=self.last_seen,
            max_severity=self.max_severity,
            alert_ids=list(self.alert_ids),
            sample_messages=list(self.sample_messages),
            closed=closed,
        )


class AlertAggregator:
    """按 (source, alert_type) 聚合；窗口锚定该组第一条告警的时间戳。"""

    def __init__(self, window_seconds):
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._window_seconds = float(window_seconds)
        self._open = {}
        self._emitted = []

    @property
    def window_seconds(self):
        return self._window_seconds

    def ingest(self, alert):
        key = (alert.source, alert.alert_type)
        window = self._open.get(key)
        if window is None:
            self._open[key] = _Window(alert.timestamp)
            window = self._open[key]
        elif alert.timestamp >= window.start + self._window_seconds:
            # 半开区间：落在边界上的告警属于下一个窗口。
            self._emitted.append(window.to_aggregated(key, closed=True))
            self._open[key] = _Window(alert.timestamp)
            window = self._open[key]
        # 注意：早于窗口起点的迟到告警仍并入当前窗口（first_seen 保持窗口锚点）。
        window.add(alert)

    def flush(self):
        """关闭所有未闭合窗口并返回本次产出的聚合告警。"""
        out = []
        for key in sorted(self._open):
            out.append(self._open[key].to_aggregated(key, closed=True))
        self._open = {}
        self._emitted.extend(out)
        return out

    @property
    def emitted(self):
        """已闭合（含 flush）的聚合告警，按 (source, alert_type, first_seen) 排序。"""
        return sorted(
            self._emitted,
            key=lambda a: (a.source, a.alert_type, a.first_seen),
        )


class AlertPipeline:
    """抑制 -> 聚合 的处理管线。

    顺序：先判定抑制（命中则只留抑制记录，不再进入聚合），
    未被抑制的告警进入聚合器，并进入“未受影响告警清单”。
    """

    def __init__(self, rules=(), window_seconds=60.0, clock=None):
        self._clock = clock if clock is not None else _default_clock
        self._engine = SuppressionEngine(rules, clock=self._clock)
        self._aggregator = AlertAggregator(window_seconds)
        self._seen_ids = set()
        self._unaffected = []  # List[AlertOccurrence]，按进入顺序

    def add_rule(self, rule):
        self._engine.add_rule(rule)

    def ingest(self, alert):
        if alert.alert_id in self._seen_ids:
            raise ValueError("duplicate alert_id: %r" % alert.alert_id)
        self._seen_ids.add(alert.alert_id)
        if self._engine.process(alert):
            return None
        self._aggregator.ingest(alert)
        occurrence = AlertOccurrence(alert=alert, group_key=(alert.source, alert.alert_type))
        self._unaffected.append(occurrence)
        return occurrence

    def flush(self):
        return self._aggregator.flush()

    @property
    def aggregated(self):
        return self._aggregator.emitted

    @property
    def suppressed(self):
        return self._engine.records

    @property
    def unaffected(self):
        """未被任何规则抑制的全部告警（完整清单，按时间戳排序）。"""
        return sorted(self._unaffected, key=lambda occ: (occ.alert.timestamp, occ.alert.alert_id))

    def open_windows(self):
        """未闭合窗口的聚合快照（closed=False），便于观察中间状态。"""
        return [
            window.to_aggregated(key, closed=False)
            for key, window in sorted(self._aggregator._open.items())
        ]


@dataclass(frozen=True)
class Reconciliation:
    """对账结果：输入告警是否被“抑制记录 + 聚合成员”不重不漏地覆盖。"""

    total_input: int
    suppressed: int
    aggregated_members: int
    missing_ids: Tuple[str, ...]
    duplicate_ids: Tuple[str, ...]

    @property
    def ok(self):
        return not self.missing_ids and not self.duplicate_ids


def reconcile(input_ids, pipeline):
    """校验每条输入告警要么被抑制、要么成为某条聚合告警的成员，且仅出现一次。"""
    accounted = []
    for record in pipeline.suppressed:
        accounted.append(record.alert.alert_id)
    for agg in pipeline.aggregated:
        accounted.extend(agg.alert_ids)
    for window in pipeline.open_windows():
        accounted.extend(window.alert_ids)

    input_set = set(input_ids)
    accounted_set = set(accounted)
    missing = tuple(sorted(input_set - accounted_set))
    seen = set()
    duplicates = []
    for alert_id in accounted:
        if alert_id in seen and alert_id not in duplicates:
            duplicates.append(alert_id)
        seen.add(alert_id)
    unexpected = accounted_set - input_set
    if unexpected:
        duplicates.extend(sorted(unexpected))
    return Reconciliation(
        total_input=len(input_ids),
        suppressed=len(pipeline.suppressed),
        aggregated_members=len(accounted) - len(pipeline.suppressed),
        missing_ids=missing,
        duplicate_ids=tuple(sorted(duplicates)),
    )


def _default_clock():
    import time

    return time.time()
