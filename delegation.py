"""权限委托与作用域检查库（仅标准库）。

核心规则：
1. 每次委托只能缩小或保持作用域（新作用域必须是委托方有效作用域的子集），
   扩大作用域的委托在创建时抛出 ScopeExpansionError。
2. 委托链的有效期取链上的最小值；请求超过父环有效期时自动收敛到父环的期限。
3. 撤销某一环后，该环及其下游全部失效（沿 parent 链检查撤销标记）。

作用域用字符串标记表示，支持层级通配符：
- "read:doc" 是具体动作；"read:*" 匹配所有以 "read:" 为前缀的动作；
- "*" 匹配全部。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


class DelegationError(Exception):
    """委托相关错误的基类。"""


class ScopeExpansionError(DelegationError):
    """新委托试图扩大作用域时抛出。"""


class RevokedError(DelegationError):
    """委托链中存在已撤销环节时抛出。"""


class ExpiredError(DelegationError):
    """委托链已过期时抛出。"""


class UnknownPrincipalError(DelegationError):
    """引用了不存在的主体/委托环节时抛出。"""


class PermissionDeniedError(DelegationError):
    """主体对指定作用域没有访问权限时抛出。"""


def _token_covers(granted: str, requested: str) -> bool:
    """判断单个授权标记是否覆盖被请求的标记。

    "*"      覆盖一切；
    "a:*"    覆盖 "a:" 前缀下的具体标记，但不覆盖 "b:*"；
    其他情况 精确相等。
    """
    if granted == "*":
        return True
    if granted.endswith(":*"):
        return requested.startswith(granted[:-1]) or requested == granted[:-1] + "*"
    return granted == requested


class Scope:
    """不可变作用域：一组授权标记。"""

    __slots__ = ("_tokens",)

    def __init__(self, *tokens: str) -> None:
        cleaned = set()
        for token in tokens:
            token = token.strip()
            if not token:
                raise ValueError("作用域标记不能为空")
            cleaned.add(token)
        self._tokens: frozenset = frozenset(cleaned)

    @property
    def tokens(self) -> frozenset:
        return self._tokens

    def covers(self, other: "Scope") -> bool:
        """self 是否覆盖 other（other 是 self 的子集）。"""
        return all(
            any(_token_covers(granted, wanted) for granted in self._tokens)
            for wanted in other._tokens
        )

    def intersection(self, other: "Scope") -> "Scope":
        """两个作用域的交集：只保留双方都能覆盖的具体标记。"""
        kept = {t for t in self._tokens
                if any(_token_covers(g, t) for g in other._tokens)}
        kept |= {t for t in other._tokens
                 if any(_token_covers(g, t) for g in self._tokens)
                 and t not in kept}
        return Scope(*sorted(kept))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Scope) and self._tokens == other._tokens

    def __hash__(self) -> int:
        return hash(self._tokens)

    def __le__(self, other: "Scope") -> bool:
        return other.covers(self)

    def __repr__(self) -> str:
        return "Scope(%s)" % ", ".join(sorted(self._tokens))


@dataclass(frozen=True)
class Edge:
    """委托链中的一环：delegator 把 scope 委托给 delegatee。"""

    id: str
    delegator: str
    delegatee: str
    scope: Scope
    expires_at: float
    parent_id: Optional[str] = None  # None 表示锚定主体（根）
    revoked: bool = False


@dataclass(frozen=True)
class Resolution:
    """一次权限解析的结果。"""

    principal: str
    effective_scope: Scope
    expires_at: float
    path: Tuple[str, ...] = field(default_factory=tuple)


class DelegationAuthority:
    """权限委托的登记、解析、撤销与访问检查。"""

    def __init__(self, clock=time.time) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        # principal -> 根锚定信息
        self._roots: Dict[str, Tuple[Scope, Optional[float]]] = {}
        # edge id -> Edge
        self._edges: Dict[str, Edge] = {}
        # delegatee -> edge id（同一主体取最新的一环）
        self._by_delegatee: Dict[str, str] = {}
        self._seq = 0

    # ---- 登记与委托 -------------------------------------------------

    def anchor(self, principal: str, scope: Scope,
               ttl: Optional[float] = None) -> None:
        """登记一个根主体自带的权限（委托链的起点）。"""
        with self._lock:
            expires_at = None if ttl is None else self._clock() + ttl
            self._roots[principal] = (scope, expires_at)

    def delegate(self, delegator: str, delegatee: str, scope: Scope,
                 ttl: Optional[float] = None) -> Edge:
        """delegator 把 scope 委托给 delegatee，返回新的一环。

        - scope 必须是 delegator 当前有效作用域的子集，否则拒绝；
        - ttl 超过链上最小值时自动收敛；
        - delegator 的链已撤销/过期/不存在时拒绝。
        """
        with self._lock:
            parent_scope, parent_expires, parent_path = self._effective(delegator)
            parent_id = parent_path[0] if parent_path else None

            if not parent_scope.covers(scope):
                raise ScopeExpansionError(
                    "委托被拒绝：%s 试图把作用域 %r 扩大到自身有效作用域 %r 之外"
                    % (delegator, scope, parent_scope)
                )

            now = self._clock()
            if ttl is None:
                expires_at = parent_expires
            else:
                if ttl <= 0:
                    raise DelegationError("ttl 必须为正数")
                requested_at = now + ttl
                if parent_expires is not None:
                    expires_at = min(requested_at, parent_expires)
                else:
                    expires_at = requested_at
            if expires_at is not None and expires_at <= now:
                raise ExpiredError("委托方的委托链已过期，不能再下发委托")

            self._seq += 1
            edge_id = "e%d" % self._seq
            edge = Edge(
                id=edge_id,
                delegator=delegator,
                delegatee=delegatee,
                scope=scope,
                expires_at=expires_at,
                parent_id=parent_id,
            )
            self._edges[edge_id] = edge
            self._by_delegatee[delegatee] = edge_id
            return edge

    # ---- 撤销 -------------------------------------------------------

    def revoke(self, edge_id: str) -> None:
        """撤销指定环节；该环及其下游全部失效。"""
        with self._lock:
            edge = self._edges.get(edge_id)
            if edge is None:
                raise UnknownPrincipalError("未知的委托环节: %s" % edge_id)
            if not edge.revoked:
                self._edges[edge_id] = Edge(
                    id=edge.id, delegator=edge.delegator,
                    delegatee=edge.delegatee, scope=edge.scope,
                    expires_at=edge.expires_at, parent_id=edge.parent_id,
                    revoked=True,
                )

    # ---- 解析与检查 -------------------------------------------------

    def resolve(self, principal: str) -> Resolution:
        """解析主体的有效权限：沿委托链向上，收敛作用域与有效期。"""
        with self._lock:
            scope, expires_at, path = self._effective(principal)
            return Resolution(
                principal=principal,
                effective_scope=scope,
                expires_at=expires_at if expires_at is not None else float("inf"),
                path=tuple(reversed(path)),
            )

    def can_access(self, principal: str, scope: Scope) -> bool:
        try:
            self.check(principal, scope)
        except DelegationError:
            return False
        return True

    def check(self, principal: str, scope: Scope) -> Resolution:
        """断言 principal 可以访问 scope，否则抛出具体错误。"""
        resolution = self.resolve(principal)
        if not resolution.effective_scope.covers(scope):
            raise PermissionDeniedError(
                "%s 的有效作用域 %r 不覆盖 %r"
                % (principal, resolution.effective_scope, scope)
            )
        return resolution

    # ---- 内部 -------------------------------------------------------

    def _effective(self, principal: str) -> Tuple[Scope, Optional[float], List[str]]:
        """沿 parent 链走到根，返回 (有效作用域, 最早过期时间, 叶子->根的环节 id)。"""
        now = self._clock()
        edge_id = self._by_delegatee.get(principal)
        path: List[str] = []

        if edge_id is None:
            if principal not in self._roots:
                raise UnknownPrincipalError("未知主体: %s" % principal)
            scope, expires_at = self._roots[principal]
            if expires_at is not None and expires_at <= now:
                raise ExpiredError("%s 的权限已过期" % principal)
            return scope, expires_at, path

        scope: Optional[Scope] = None
        expires_at: Optional[float] = None
        current = edge_id
        while current is not None:
            edge = self._edges.get(current)
            if edge is None:  # 理论上不可达：链断裂
                raise UnknownPrincipalError("委托链断裂: 缺少环节 %s" % current)
            if edge.revoked:
                raise RevokedError(
                    "委托环节 %s（%s -> %s）已撤销，其下游全部失效"
                    % (edge.id, edge.delegator, edge.delegatee)
                )
            if edge.expires_at is not None and edge.expires_at <= now:
                raise ExpiredError("委托环节 %s 已过期" % edge.id)

            path.append(edge.id)
            scope = edge.scope if scope is None else scope.intersection(edge.scope)
            expires_at = self._min_expiry(expires_at, edge.expires_at)
            current = edge.parent_id

        return scope, expires_at, path

    @staticmethod
    def _min_expiry(a: Optional[float], b: Optional[float]) -> Optional[float]:
        if a is None:
            return b
        if b is None:
            return a
        return min(a, b)
