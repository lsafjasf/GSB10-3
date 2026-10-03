"""Reentrant read/write lock.

所有权模型(owner)
    持有者以线程身份标识(``threading.get_ident``); 也允许通过 ``owner=``
    传入显式标识(例如协程 id)。

每个持有者维护一个加锁栈, 栈帧为 ``READ`` / ``WRITE`` / ``SUBSUMED_READ``:

* 读锁: ``acquire_read`` 压入 READ, 同持有者可任意重入, 读深度 = READ 帧数。
* 写锁: ``acquire_write`` 压入 WRITE, 同持有者可任意重入, 写深度 = WRITE 帧数。
* 释放按"栈顶匹配"校验: ``release_read`` 必须匹配读帧,
  ``release_write`` 必须匹配 WRITE 帧, 顺序错误直接报错, 不允许越权释放。

明确规则
1. 写持有期间再请求读锁(规则 R3, 非降级):
   不会"悄悄降级"写锁, 也不会阻塞自杀。记为 SUBSUMED_READ(并入) 读帧:
   它只是把读意图压栈, 全局互斥状态仍由写锁保持, 直到写深度归零。
   依据: 写锁本身已经包含读权限(写者可读), 读请求在写临界区内恒真;
   该读帧不增加全局读计数, 释放它也不唤醒任何等待者。
2. 降级必须显式调用 ``downgrade``: 当且仅当写深度恰为 1 时允许,
   把唯一写帧原子换成读帧并立即交出写互斥。若仍有重入写帧,
   降级被拒绝(必须先把写重入逐层释放), 避免"夹在写帧中的读帧"
   这种含糊语义。
3. 升级必须显式调用 ``upgrade``。为避免经典的"两个读者同时等写锁"
   自死锁: 只允许该持有者是唯一读者、持有恰好一层读锁(无重入、
   无并入读帧)、且当前没有等待写者时升级; 其余情况立即抛
   ``LockUpgradeError``, 绝不阻塞。
4. 不支持的组合一律抛异常, 不做隐式阻塞/隐式转换:
   读持有期间直接 ``acquire_write`` -> ``LockUpgradeError``。

公平性: 写者优先。已有等待写者时, 新进读者(非持有者重入)等待,
防止写者饥饿。
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Dict, List, Optional

__all__ = [
    "ReentrantRWLock",
    "LockError",
    "LockUpgradeError",
    "LockReleaseError",
    "LockTimeoutError",
]

READ = "READ"
WRITE = "WRITE"
SUBSUMED_READ = "SUBSUMED_READ"

_USE_THREAD_OWNER = object()


class LockError(RuntimeError):
    """锁相关错误的基类。"""


class LockUpgradeError(LockError):
    """不被支持的升级/降级组合。"""


class LockReleaseError(LockError):
    """释放与持有不匹配(未持有 / 栈顶类型错误 / 跨持有者释放)。"""


class LockTimeoutError(LockError):
    """在给定超时内未能获取锁。"""


class ReentrantRWLock:
    def __init__(self) -> None:
        self._cond = threading.Condition(threading.Lock())
        self._writer: Optional[int] = None
        self._write_depth = 0
        self._read_depths: Dict[int, int] = {}
        self._stacks: Dict[int, List[str]] = {}
        self._waiting_writers = 0

    # ---- 内部工具 -------------------------------------------------------

    def _owner_key(self, owner) -> int:
        if owner is _USE_THREAD_OWNER:
            return threading.get_ident()
        return owner

    def _stack(self, owner: int) -> List[str]:
        stack = self._stacks.get(owner)
        if stack is None:
            stack = []
            self._stacks[owner] = stack
        return stack

    def _check_invariants(self) -> None:
        """调用方必须已持有 ``self._cond``。每次状态变更后断言。"""
        if self._writer is not None:
            assert self._write_depth > 0
            assert not self._read_depths, "写者持有时不得存在真实读者"
            stack = self._stacks.get(self._writer, [])
            assert stack.count(WRITE) == self._write_depth
            # 写临界区内只可能存在 SUBSUMED_READ, 不可能存在真实 READ。
            assert stack.count(READ) == 0
        else:
            assert self._write_depth == 0
            assert self._waiting_writers >= 0
        for owner, depth in self._read_depths.items():
            stack = self._stacks.get(owner, [])
            assert depth > 0
            assert depth == stack.count(READ), (owner, depth, stack)

    # ---- 深度查询 -------------------------------------------------------

    def read_depth(self, owner=_USE_THREAD_OWNER) -> int:
        owner = self._owner_key(owner)
        with self._cond:
            return self._stacks.get(owner, []).count(READ)

    def write_depth(self, owner=_USE_THREAD_OWNER) -> int:
        owner = self._owner_key(owner)
        with self._cond:
            return self._stacks.get(owner, []).count(WRITE)

    def subsumed_read_depth(self, owner=_USE_THREAD_OWNER) -> int:
        owner = self._owner_key(owner)
        with self._cond:
            return self._stacks.get(owner, []).count(SUBSUMED_READ)

    def snapshot(self, owner=_USE_THREAD_OWNER) -> Dict[str, int]:
        owner = self._owner_key(owner)
        with self._cond:
            stack = list(self._stacks.get(owner, ()))
            return {
                "read": stack.count(READ),
                "write": stack.count(WRITE),
                "subsumed_read": stack.count(SUBSUMED_READ),
                "global_write_depth": self._write_depth,
                "global_reader_count": len(self._read_depths),
                "is_writer": 1 if self._writer == owner else 0,
            }

    # ---- 读锁 -----------------------------------------------------------

    def acquire_read(self, timeout: Optional[float] = None,
                     owner=_USE_THREAD_OWNER) -> None:
        owner = self._owner_key(owner)
        with self._cond:
            stack = self._stack(owner)
            # R2 读重入: 同持有者直接压栈。
            if owner in self._read_depths:
                stack.append(READ)
                self._read_depths[owner] += 1
                self._check_invariants()
                return
            # R3 写持有期间取读: SUBSUMED_READ, 保持互斥, 不阻塞。
            if self._writer == owner:
                stack.append(SUBSUMED_READ)
                self._check_invariants()
                return

            deadline = None if timeout is None else time.monotonic() + timeout
            while self._writer is not None or self._waiting_writers > 0:
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise LockTimeoutError("获取读锁超时")
                else:
                    remaining = None
                self._cond.wait(timeout=remaining)

            stack.append(READ)
            self._read_depths[owner] = 1
            self._check_invariants()

    def release_read(self, owner=_USE_THREAD_OWNER) -> None:
        owner = self._owner_key(owner)
        with self._cond:
            stack = self._stacks.get(owner)
            if not stack:
                raise LockReleaseError("当前持有者没有未释放的读锁")
            frame = stack[-1]
            if frame == WRITE:
                raise LockReleaseError(
                    "栈顶是写锁, 不能用 release_read 释放; 写锁必须显式按序释放")
            stack.pop()
            if frame == SUBSUMED_READ:
                # 并入读帧: 不触碰全局读计数, 也不唤醒任何人。
                assert self._writer == owner or owner in self._read_depths
            else:
                depth = self._read_depths[owner] - 1
                if depth == 0:
                    del self._read_depths[owner]
                    self._cond.notify_all()
                else:
                    self._read_depths[owner] = depth
            self._check_invariants()

    # ---- 写锁 -----------------------------------------------------------

    def acquire_write(self, timeout: Optional[float] = None,
                      owner=_USE_THREAD_OWNER) -> None:
        owner = self._owner_key(owner)
        with self._cond:
            stack = self._stack(owner)
            # W2 写重入。
            if self._writer == owner:
                stack.append(WRITE)
                self._write_depth += 1
                self._check_invariants()
                return
            # 读持有期间直接取写锁 = 隐式升级请求, 显式拒绝。
            if owner in self._read_depths:
                raise LockUpgradeError(
                    "读锁持有期间不能直接 acquire_write; 请显式调用 upgrade()")

            self._waiting_writers += 1
            deadline = None if timeout is None else time.monotonic() + timeout
            acquired = False
            try:
                while self._writer is not None or self._read_depths:
                    if deadline is not None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise LockTimeoutError("获取写锁超时")
                    else:
                        remaining = None
                    self._cond.wait(timeout=remaining)
                self._writer = owner
                self._write_depth = 1
                stack.append(WRITE)
                acquired = True
            finally:
                self._waiting_writers -= 1
                if not acquired:
                    # 超时退出, 唤醒在同条件上等待的其他人。
                    self._cond.notify_all()
            self._check_invariants()

    def release_write(self, owner=_USE_THREAD_OWNER) -> None:
        owner = self._owner_key(owner)
        with self._cond:
            stack = self._stacks.get(owner)
            if not stack or WRITE not in stack:
                raise LockReleaseError("当前持有者没有未释放的写锁")
            if stack[-1] != WRITE:
                raise LockReleaseError(
                    "栈顶是读帧, 不能用 release_write 释放; 读帧必须先按序释放")
            stack.pop()
            self._write_depth -= 1
            if self._write_depth == 0:
                self._writer = None
                self._cond.notify_all()
            self._check_invariants()

    # ---- 显式升级 / 降级 ------------------------------------------------

    def upgrade(self, owner=_USE_THREAD_OWNER) -> None:
        """读 -> 写 显式升级。

        仅当本持有者是全局唯一读者, 且恰好持有一层读锁(无重入读帧、
        无并入读帧), 并且当前没有等待写者时才允许; 否则立即抛
        ``LockUpgradeError``, 绝不阻塞(阻塞式升级与其他读者构成自死锁)。
        """
        owner = self._owner_key(owner)
        with self._cond:
            stack = self._stacks.get(owner)
            if self._writer == owner:
                raise LockUpgradeError("已经持有写锁, 无需升级")
            if self._writer is not None:
                raise LockUpgradeError("其他持有者正在写, 无法升级")
            if owner not in self._read_depths:
                raise LockUpgradeError("未持有读锁, 无法升级")
            if len(self._read_depths) != 1:
                raise LockUpgradeError(
                    "存在其他读者, 阻塞式升级会造成相互等待, 拒绝升级")
            if len(stack) != 1 or stack[0] != READ:
                raise LockUpgradeError(
                    "存在读重入或写期间并入读帧时不允许升级, "
                    "请先释放到单层读锁")
            if self._waiting_writers:
                raise LockUpgradeError(
                    "已有写者在等待; 此时升级会破坏写者优先策略")
            stack[0] = WRITE
            del self._read_depths[owner]
            self._writer = owner
            self._write_depth = 1
            self._check_invariants()

    def downgrade(self, owner=_USE_THREAD_OWNER) -> None:
        """写 -> 读 显式降级。

        当且仅当写深度恰为 1(无重入写帧)时允许: 把唯一写帧原子换成
        读帧, 立即交出写互斥并成为读者, 同时唤醒等待者。
        仍有重入写帧时拒绝降级, 必须先逐层 release_write。
        """
        owner = self._owner_key(owner)
        with self._cond:
            stack = self._stacks.get(owner)
            if not stack or stack[-1] != WRITE:
                raise LockUpgradeError("栈顶不是写锁, 无法降级")
            if self._write_depth != 1:
                raise LockUpgradeError(
                    "仍有重入写锁未释放时不允许降级; "
                    "请先 release_write 到写深度归零后再 downgrade")
            stack[-1] = READ
            self._write_depth = 0
            self._writer = None
            self._read_depths[owner] = self._read_depths.get(owner, 0) + 1
            self._cond.notify_all()
            self._check_invariants()

    # ---- 上下文管理器 ---------------------------------------------------

    @contextmanager
    def read_lock(self, owner=_USE_THREAD_OWNER):
        self.acquire_read(owner=owner)
        try:
            yield self
        finally:
            self.release_read(owner=owner)

    @contextmanager
    def write_lock(self, owner=_USE_THREAD_OWNER):
        self.acquire_write(owner=owner)
        try:
            yield self
        finally:
            self.release_write(owner=owner)
