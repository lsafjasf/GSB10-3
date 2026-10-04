"""权限委托与作用域检查库（仅标准库）。

作用域模型
----------
每条权限是 ``resource:action`` 形式的字符串，动作部分支持后缀 ``*``
作为单段通配（如 ``doc:read*`` 匹配 ``doc:read`` / ``doc:readonly``，
但不匹配 ``doc:share/revoke`` 这样的下一段动作）。一个作用域是若干条
权限的集合，用前缀 ``*`` 表示空集合是不存在的，空集合请直接传空。

安全规则
--------
1. 每次委托只能缩小或保持作用域（子集关系），扩大直接抛
   ``ScopeWideningError``，并通过 ``leaked()`` 给出试图越权的权限。
2. 时间上同样只能收敛：被委托方请求的有效期若超过父链有效截止时间，
   会被收敛（截断）到父链截止时间，链上实际有效期取整条链的最小值。
3. 任意一环被撤销（或过期），该环及其所有下级链全部失效。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

__all__ = [
    "Scope",
    "ScopeWideningError",
    "InvalidScopeError",
    "DelegationError",
    "Link",
    "Decision",
    "DelegationRegistry",
]


class DelegationError(Exception):
    """委托相关错误的基类。"""


class InvalidScopeError(DelegationError):
    """权限或作用域字符串非法。"""


class ScopeWideningError(DelegationError):
    """委托时作用域相对父作用域发生了扩大。

    ``leaked`` 即“父作用域没有、但本次想下发”的越权权限集合。
    """

    def __init__(self, message: str, leaked: Iterable[str] = ()):
        super().__init__(message)
        self.leaked = tuple(leaked)


def _parse_entry(entry: str) -> Tuple[str, str]:
    if not isinstance(entry, str):
        raise InvalidScopeError(f"权限必须是字符串: {entry!r}")
    text = entry.strip()
    if ":" not in text:
        raise InvalidScopeError(f"权限必须是 resource:action 形式: {entry!r}")
    resource, action = text.split(":", 1)
    resource = resource.strip()
    action = action.strip()
    if not resource or not action:
        raise InvalidScopeError(f"资源和动作都不能为空: {entry!r}")
    if resource == "*" or action == "**":
        raise InvalidScopeError(f"不支持的权限写法: {entry!r}")
    if "*" in resource:
        raise InvalidScopeError(f"资源部分不支持通配: {entry!r}")
    if "*" in action and not action.endswith("*"):
        raise InvalidScopeError(f"动作仅支持结尾单段通配: {entry!r}")
    return resource, action


def _covers(granted: Tuple[str, str], required: Tuple[str, str]) -> bool:
    g_res, g_act = granted
    r_res, r_act = required
    if g_res != r_res:
        return False
    if g_act.endswith("*"):
        prefix = g_act[:-1]
        remainder = r_act[len(prefix):] if r_act.startswith(prefix) else None
        # 单段通配：余下部分不得跨 "/" 段
        return remainder is not None and "/" not in remainder
    return g_act == r_act


class Scope:
    """不可变作用域：若干 ``resource:action`` 权限的规范化集合。"""

    __slots__ = ("_entries",)

    def __init__(self, entries: Iterable[str] = ()):
        normalized = frozenset(_parse_entry(item) for item in entries)
        object.__setattr__(self, "_entries", normalized)

    entries: Tuple[str, ...] = property(lambda self: tuple(sorted(
        f"{res}:{act}" for res, act in self._entries
    )))

    def covers_permission(self, permission: str) -> bool:
        required = _parse_entry(permission)
        return any(_covers(granted, required) for granted in self._entries)

    def is_subset_of(self, other: "Scope") -> bool:
        return all(
            any(_covers(upstream, entry) for upstream in other._entries)
            for entry in self._entries
        )

    def leaked(self, other: "Scope") -> Tuple[str, ...]:
        """返回 self 中不被 other 覆盖的权限（即越权部分）。"""
        return tuple(sorted(
            f"{res}:{act}"
            for res, act in self._entries
            if not any(_covers(upstream, (res, act)) for upstream in other._entries)
        ))

    def render(self) -> str:
        if not self._entries:
            return "∅"
        return "{" + ", ".join(self.entries) + "}"

    def __contains__(self, permission: object) -> bool:
        if not isinstance(permission, str):
            return False
        try:
            return self.covers_permission(permission)
        except InvalidScopeError:
            return False

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Scope) and self._entries == other._entries

    def __hash__(self) -> int:
        return hash(self._entries)

    def __repr__(self) -> str:
        return f"Scope({list(self.entries)!r})"


@dataclass(frozen=True)
class Link:
    """委托链上的一环。link_id 为 'root' 表示源头授权。"""

    link_id: str
    delegator: Optional[str]
    delegatee: str
    scope: Scope
    expires_at: float
    parent_id: Optional[str]
    revoked: bool = False


@dataclass(frozen=True)
class Decision:
    """一次权限判定的结果。"""

    allowed: bool
    reason: str = ""
    chain: Tuple[Link, ...] = field(default_factory=tuple)
    effective_scope: Optional[Scope] = None
    effective_expires_at: Optional[float] = None
    broken_link_id: Optional[str] = None

    @property
    def denied(self) -> bool:
        return not self.allowed


class DelegationRegistry:
    """委托图：root 授权 -> 多级委托 -> 权限判定与撤销。"""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._links: Dict[str, Link] = {}

    # ---- 基础查询 -------------------------------------------------------

    def now(self) -> float:
        return self._clock()

    def get_link(self, link_id: str) -> Link:
        if link_id not in self._links:
            raise DelegationError(f"委托环不存在: {link_id}")
        return self._links[link_id]

    def children_of(self, link_id: str) -> Tuple[str, ...]:
        return tuple(sorted(
            lid for lid, link in self._links.items() if link.parent_id == link_id
        ))

    # ---- 建立委托 -------------------------------------------------------

    def grant_root(
        self,
        delegatee: str,
        permissions: Iterable[str],
        expires_at: float,
        link_id: str = "root",
    ) -> str:
        """颁发源头权限（链的根）。"""
        if link_id in self._links:
            raise DelegationError(f"委托环已存在: {link_id}")
        if expires_at <= self._clock():
            raise DelegationError("源头授权的有效期必须在未来")
        scope = Scope(permissions)
        self._links[link_id] = Link(
            link_id=link_id,
            delegator=None,
            delegatee=delegatee,
            scope=scope,
            expires_at=float(expires_at),
            parent_id=None,
        )
        return link_id

    def delegate(
        self,
        parent_id: str,
        delegatee: str,
        permissions: Iterable[str],
        expires_at: float,
        link_id: str,
        actor: Optional[str] = None,
    ) -> str:
        """从父环再委托一环。作用域扩大将被拒绝；有效期只收敛不放大。"""
        if link_id in self._links:
            raise DelegationError(f"委托环已存在: {link_id}")
        chain = self.chain(parent_id)
        parent = self._links[parent_id]

        if actor is not None and actor != parent.delegatee:
            raise DelegationError(
                f"只有父环持有者 {parent.delegatee!r} 可以继续委托，"
                f"实际操作者 {actor!r}"
            )

        requested_scope = Scope(permissions)
        # 父链的有效作用域 = 链上所有作用域的交集（根到父），
        # 这里取链上最末端（最小）的一环即可，因为每环都已保证是子集。
        parent_scope = chain[-1].scope
        leaked = requested_scope.leaked(parent_scope)
        if leaked:
            raise ScopeWideningError(
                f"委托 {link_id!r} 越权扩大：以下权限不在父作用域 "
                f"{parent_scope.render()} 内: {list(leaked)}",
                leaked=leaked,
            )

        if expires_at <= self._clock():
            raise DelegationError("委托的有效期必须在未来")

        # 时间收敛：请求的截止时间超过父链有效截止时间时，截断到最小值。
        parent_effective_expiry = min(link.expires_at for link in chain)
        granted_expiry = min(float(expires_at), parent_effective_expiry)

        self._links[link_id] = Link(
            link_id=link_id,
            delegator=parent.delegatee,
            delegatee=delegatee,
            scope=requested_scope,
            expires_at=granted_expiry,
            parent_id=parent_id,
        )
        return link_id

    # ---- 撤销 -----------------------------------------------------------

    def revoke(self, link_id: str) -> None:
        """撤销一环；其所有下级环在判定时随之失效。"""
        link = self.get_link(link_id)
        if link.revoked:
            return
        self._links[link_id] = Link(**{**link.__dict__, "revoked": True})

    # ---- 链与判定 -------------------------------------------------------

    def chain(self, link_id: str) -> List[Link]:
        """返回根 -> link_id 的有序链；环不存在时抛错。"""
        ordered: List[Link] = []
        current: Optional[str] = link_id
        seen = set()
        while current is not None:
            if current in seen:
                raise DelegationError(f"委托链存在环: {current}")
            seen.add(current)
            link = self.get_link(current)
            ordered.append(link)
            current = link.parent_id
        ordered.reverse()
        return ordered

    def inspect(self, link_id: str, at: Optional[float] = None) -> Decision:
        """判定某条链当前是否有效，并给出收敛后的作用域与有效期。"""
        now = self._clock() if at is None else at
        chain = self.chain(link_id)

        effective_expiry = min(link.expires_at for link in chain)
        for link in chain:
            if link.revoked:
                return Decision(
                    allowed=False,
                    reason=f"委托环 {link.link_id!r} 已被撤销，下级全部失效",
                    chain=tuple(chain),
                    effective_expires_at=effective_expiry,
                    broken_link_id=link.link_id,
                )
        if now >= effective_expiry:
            expired = min(chain, key=lambda item: item.expires_at)
            return Decision(
                allowed=False,
                reason=(
                    f"委托链已过期：最短有效期来自 {expired.link_id!r}，"
                    f"截止于 {effective_expiry:g}（当前 {now:g}）"
                ),
                chain=tuple(chain),
                effective_expires_at=effective_expiry,
                broken_link_id=expired.link_id,
            )

        # 每环都是父作用域的子集，末端即全链交集（最小作用域）。
        effective_scope = chain[-1].scope
        return Decision(
            allowed=True,
            reason="ok",
            chain=tuple(chain),
            effective_scope=effective_scope,
            effective_expires_at=effective_expiry,
        )

    def can(
        self,
        link_id: str,
        permission: str,
        at: Optional[float] = None,
    ) -> Decision:
        """判定某条链是否可以行使某条具体权限。"""
        decision = self.inspect(link_id, at=at)
        if not decision.allowed:
            return decision
        assert decision.effective_scope is not None
        if not decision.effective_scope.covers_permission(permission):
            return Decision(
                allowed=False,
                reason=(
                    f"权限 {permission!r} 不在链有效作用域 "
                    f"{decision.effective_scope.render()} 内"
                ),
                chain=decision.chain,
                effective_scope=decision.effective_scope,
                effective_expires_at=decision.effective_expires_at,
            )
        return decision

    def check(
        self,
        link_id: str,
        permission: str,
        at: Optional[float] = None,
    ) -> Decision:
        """can 的严格版：拒绝时抛 PermissionError。"""
        decision = self.can(link_id, permission, at=at)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        return decision
