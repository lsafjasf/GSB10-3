"""分级 + 分通道采样规则引擎。

匹配优先级（数字越小优先级越高；同级时 rules 列表中更靠前的优先）：
    0  强制保留规则（rule.keep=True 且 rule.rate=1.0）或受保护通道/路径
       —— ERROR / FATAL 级别也无条件走这一档：错误日志绝不参与采样。
    1  级别 + 通道（精确）
    2  仅级别
    3  仅通道（精确）
    4  默认采样率

“关键路径不丢”通过强制保留规则实现：level=None, channel=<name>, keep=True
或 path_prefix 前缀命中。
规则通过 LayeredSampler.update_rules() 原子替换，每次替换产生新的版本号，
进行中的请求持有旧版本快照，更新不影响其一致性。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Iterable, Optional, Sequence


class Level(IntEnum):
    """日志级别，数值越大越严重。"""

    DEBUG = 10
    INFO = 20
    WARN = 30
    ERROR = 40
    FATAL = 50

    @classmethod
    def normalize(cls, value: "Level | int | str") -> "Level":
        if isinstance(value, Level):
            return value
        if isinstance(value, int):
            return cls(value)
        if isinstance(value, str):
            return cls[value.strip().upper()]
        raise TypeError(f"不支持的级别类型: {type(value)!r}")


@dataclass(frozen=True)
class Rule:
    """一条采样规则。

    level/channel 均为 None 表示通配；rate 为命中该规则时的保留概率。
    keep=True 是 rate=1.0 的语义化写法（强制保留，错误/关键路径用）。
    drop=True 是 rate=0.0 的语义化写法（强制丢弃）。
    path_prefix 非空时，事件路径以该前缀开头才算命中。
    """

    level: Optional[Level] = None
    channel: Optional[str] = None
    rate: Optional[float] = None
    keep: bool = False
    drop: bool = False
    path_prefix: Optional[str] = None
    name: str = ""

    def __post_init__(self) -> None:
        filled = [v is not None for v in (self.rate,)]
        if sum(filled) > 1 or (self.keep and self.drop) or (
            self.keep and self.rate is not None and self.rate != 1.0
        ) or (self.drop and self.rate is not None and self.rate != 0.0):
            raise ValueError(
                f"规则 {self.name or '<unnamed>'} 的 rate/keep/drop 配置冲突"
            )
        rate = self.effective_rate
        if not 0.0 <= rate <= 1.0:
            raise ValueError(f"规则 {self.name or '<unnamed>'} 的 rate 越界: {rate}")
        if self.level is not None and not isinstance(self.level, Level):
            raise TypeError("rule.level 必须是 Level 或 None")

    @property
    def effective_rate(self) -> float:
        if self.keep:
            return 1.0
        if self.drop:
            return 0.0
        if self.rate is None:
            raise ValueError(
                f"规则 {self.name or '<unnamed>'} 必须提供 rate/keep/drop 之一"
            )
        return float(self.rate)

    @property
    def forced_keep(self) -> bool:
        return self.effective_rate == 1.0


@dataclass(frozen=True)
class Match:
    """一次规则匹配的结果：保留概率 + 命中规则 + 优先级档位。"""

    rate: float
    rule: Optional[Rule]
    priority: int  # 0=强制保留(含错误级别), 1=级别+通道, 2=级别, 3=通道, 4=默认

    @property
    def forced_keep(self) -> bool:
        return self.rate == 1.0 and self.priority == 0


@dataclass(frozen=True)
class Event:
    """一条日志事件。"""

    request_id: str
    level: Level
    channel: str
    message: str = ""
    path: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.level, Level):
            object.__setattr__(self, "level", Level.normalize(self.level))

    @property
    def group_key(self) -> "tuple[Level, str]":
        return self.level, self.channel


@dataclass(frozen=True)
class _Snapshot:
    version: int
    rules: Sequence[Rule]
    default_rate: float
    protected_channels: frozenset[str]
    protected_path_prefixes: tuple[str, ...]
    min_forced_level: Level


_PRIORITY_FORCED = 0
_PRIORITY_LEVEL_CHANNEL = 1
_PRIORITY_LEVEL = 2
_PRIORITY_CHANNEL = 3
_PRIORITY_DEFAULT = 4


class LayeredSampler:
    """线程安全的分级/分通道采样器。

    protected_channels / protected_path_prefixes 声明“关键路径”：
    命中的事件一律强制保留（优先级 0），即使规则后来被更新也不会被采样。
    min_forced_level 及以上级别默认强制保留（默认 ERROR），保证错误日志不采。
    """

    def __init__(
        self,
        rules: Iterable[Rule] = (),
        default_rate: float = 1.0,
        protected_channels: Iterable[str] = (),
        protected_path_prefixes: Iterable[str] = (),
        min_forced_level: Level = Level.ERROR,
        rng: Optional[Callable[[], float]] = None,
    ) -> None:
        if not 0.0 <= default_rate <= 1.0:
            raise ValueError("default_rate 必须在 [0, 1]")
        self._rng = rng or __import__("random").Random(0).random
        self._lock = threading.RLock()
        self._snapshot = self._build(
            0,
            list(rules),
            default_rate,
            frozenset(protected_channels),
            tuple(protected_path_prefixes),
            Level.normalize(min_forced_level),
        )

    @staticmethod
    def _build(
        version: int,
        rules: Sequence[Rule],
        default_rate: float,
        protected_channels: frozenset[str],
        protected_path_prefixes: tuple[str, ...],
        min_forced_level: Level,
    ) -> _Snapshot:
        for rule in rules:
            if not isinstance(rule, Rule):
                raise TypeError("rules 中的元素必须是 Rule")
        return _Snapshot(
            version=version,
            rules=tuple(rules),
            default_rate=default_rate,
            protected_channels=protected_channels,
            protected_path_prefixes=tuple(
                dict.fromkeys(protected_path_prefixes)
            ),  # 去重保序
            min_forced_level=min_forced_level,
        )

    # ---- 规则管理 -------------------------------------------------------

    @property
    def version(self) -> int:
        return self._snapshot.version

    @property
    def snapshot(self) -> _Snapshot:
        """当前规则快照（请求期间持有它即可隔离后续更新）。"""
        return self._snapshot

    def update_rules(
        self,
        rules: Iterable[Rule],
        default_rate: Optional[float] = None,
        protected_channels: Optional[Iterable[str]] = None,
        protected_path_prefixes: Optional[Iterable[str]] = None,
        min_forced_level: "Level | None" = None,
    ) -> int:
        """原子替换规则，返回新版本号。未传入的项沿用旧配置。"""
        with self._lock:
            old = self._snapshot
            self._snapshot = self._build(
                old.version + 1,
                list(rules),
                old.default_rate if default_rate is None else default_rate,
                old.protected_channels
                if protected_channels is None
                else frozenset(protected_channels),
                old.protected_path_prefixes
                if protected_path_prefixes is None
                else tuple(protected_path_prefixes),
                old.min_forced_level
                if min_forced_level is None
                else Level.normalize(min_forced_level),
            )
            return self._snapshot.version

    # ---- 匹配 -----------------------------------------------------------

    def match(
        self,
        event: "Event | Level",
        channel: "str | None" = None,
        path: str = "",
        snapshot: "Optional[_Snapshot]" = None,
    ) -> Match:
        """返回事件的采样决策。snapshot 可指定使用某个历史版本的规则。"""
        snap = snapshot or self._snapshot
        if isinstance(event, Event):
            level, chan, p = event.level, event.channel, event.path or path
        else:
            if channel is None:
                raise ValueError("非 Event 输入必须提供 channel")
            level, chan, p = Level.normalize(event), channel, path

        # 档位 0a：错误及以上级别无条件保留 —— 错误日志绝不采样。
        if level >= snap.min_forced_level:
            return Match(1.0, None, _PRIORITY_FORCED)

        # 档位 0b：关键路径（受保护通道/路径前缀）。
        if chan in snap.protected_channels:
            return Match(1.0, None, _PRIORITY_FORCED)
        if p and snap.protected_path_prefixes and p.startswith(
            tuple(snap.protected_path_prefixes)
        ):
            return Match(1.0, None, _PRIORITY_FORCED)

        # 显式强制保留规则（rate=1.0 的 keep 规则）优先于普通采样规则，
        # 这样“排查关键路径”的规则永远赢，且优先级顺序对调用方可解释。
        for rule in snap.rules:
            if rule.forced_keep and _rule_hits(rule, level, chan, p):
                return Match(1.0, rule, _PRIORITY_FORCED)

        # 档位 1：级别 + 通道（path_prefix 也可附带）。
        for rule in snap.rules:
            if (
                rule.level is level
                and rule.channel is not None
                and chan == rule.channel
                and _path_ok(rule, p)
            ):
                return Match(rule.effective_rate, rule, _PRIORITY_LEVEL_CHANNEL)

        # 档位 2：仅级别。
        for rule in snap.rules:
            if (
                rule.level is level
                and rule.channel is None
                and _path_ok(rule, p)
            ):
                return Match(rule.effective_rate, rule, _PRIORITY_LEVEL)

        # 档位 3：仅通道。
        for rule in snap.rules:
            if (
                rule.level is None
                and rule.channel is not None
                and chan == rule.channel
                and _path_ok(rule, p)
            ):
                return Match(rule.effective_rate, rule, _PRIORITY_CHANNEL)

        return Match(snap.default_rate, None, _PRIORITY_DEFAULT)

    def decide_event(
        self, event: Event, snapshot: "Optional[_Snapshot]" = None
    ) -> "tuple[bool, Match]":
        """单事件级别的随机决策（无请求一致性保证，仅供 head 模式复用）。"""
        m = self.match(event, snapshot=snapshot)
        if m.rate in (0.0, 1.0):
            return m.rate == 1.0, m
        return self._rng() < m.rate, m


def _path_ok(rule: Rule, path: str) -> bool:
    return rule.path_prefix is None or (
        bool(path) and path.startswith(rule.path_prefix)
    )


def _rule_hits(rule: Rule, level: Level, channel: str, path: str) -> bool:
    if rule.level is not None and rule.level is not level:
        return False
    if rule.channel is not None and rule.channel != channel:
        return False
    return _path_ok(rule, path)


class InconsistentRequestError(AssertionError):
    """同一请求的日志出现一部分保留、一部分丢弃时抛出。"""

    def __init__(self, request_id: str, kept: int, dropped: int) -> None:
        super().__init__(
            f"请求 {request_id!r} 违反请求级一致性: 保留 {kept} 条 / 丢弃 {dropped} 条，"
            "同一请求必须全留或全丢"
        )
        self.request_id = request_id
        self.kept = kept
        self.dropped = dropped


def assert_request_consistency(
    request_id: str, decisions: Iterable[bool]
) -> None:
    """请求级一致性断言：decisions 是该请求每条日志的保留/丢弃标记。

    非空序列中必须全部为 True 或全部为 False，否则抛 InconsistentRequestError。
    """
    flags = list(decisions)
    if not flags:
        return
    kept = sum(1 for f in flags if f)
    dropped = len(flags) - kept
    if kept and dropped:
        raise InconsistentRequestError(request_id, kept, dropped)
