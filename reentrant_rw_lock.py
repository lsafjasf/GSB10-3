"""ReentrantReadWriteLock —— 可重入读写锁（仅标准库实现）。

语义规则（显式约定，未支持的组合直接抛错，绝不静默阻塞/降级）：

1. 读锁、写锁分别按"持有者线程 + 重入深度"记账：
   - 读锁: {thread_ident: depth}，可有多个持有者。
   - 写锁: 同时只允许一个持有者，持有者内部记录重入深度。
   - 释放时只减深度，深度归零才是真正释放并唤醒等待者。

2. 重入同类型锁：
   - 持有读锁的线程再次 acquire_read：直接 +1，永远不被等待的写者
     阻塞（否则线程会和自己死锁）；写者优先策略只对"新读者"生效。
   - 持有写锁的线程再次 acquire_write：写深度 +1，权限不降级。

3. 写者再拿读锁（写 -> 读，允许，显式记账，不降级）：
   - 依据：写锁在互斥性上强于读锁，"正在写"必然满足"正在读"所需
     的全部前提（没有任何其他读者/写者在场）。这与
     Java ReentrantReadWriteLock 的做法一致（写锁可安全获得读锁）。
   - 实现上把它记入读锁表（独立深度），写锁本身原样保留、写深度不变；
     这不是降级：释放该读锁后，线程仍然独占写锁，其他线程依然进不来。

4. 读者再拿写锁（读 -> 写，升级）：
   - 仅当该线程是当前唯一读者时允许就地升级；
   - 若还有其他读者，立即抛 UpgradeError，而不是等待
     （等待会与"其他读者也在尝试升级/等待本线程释放读锁"形成经典
     升级死锁）。

5. 显式降级 downgrade()：写 -> 读的永久转换，用独立方法显式建模，
   要求写深度恰好为 1（存在未结清的重入写时不允许降级，防止深度语义
   被静默改写）；降级后写锁真正释放并唤醒等待者，本线程转为持有读锁。
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Optional


class RWLockError(RuntimeError):
    """本模块所有锁错误的基类。"""


class ReleaseUnheldError(RWLockError):
    """释放自己并不持有的锁。"""


class UpgradeError(RWLockError):
    """读 -> 写升级不成立（存在其他读者）。"""


class DowngradeError(RWLockError):
    """写 -> 读降级不成立（写重入深度不为 1）。"""


class AcquireTimeout(RWLockError):
    """非阻塞获取失败（timeout=0 时拿不到锁）。"""


class ReentrantReadWriteLock:
    """线程内可重入的读写锁。

    调度策略：写者优先。有等待的写者时，新读者排队；但已经持有读锁的
    线程重入读锁、以及持写锁的线程重入，始终直接放行以避免自死锁。
    """

    def __init__(self) -> None:
        self._cond = threading.Condition(threading.Lock())
        self._readers: dict[int, int] = {}   # thread_ident -> 读重入深度
        self._writer: Optional[int] = None  # 当前写持有者
        self._write_depth: int = 0           # 写重入深度（仅 _writer 有意义）
        self._writers_waiting: int = 0

    # ---------- 状态查询（供断言/自测使用） ----------

    def read_depth(self, ident: Optional[int] = None) -> int:
        """某线程当前持有的读锁深度（默认调用线程）。"""
        ident = threading.get_ident() if ident is None else ident
        with self._cond:
            return self._readers.get(ident, 0)

    def write_depth(self, ident: Optional[int] = None) -> None | int:
        """某线程当前持有的写锁深度；非写持有者返回 0。"""
        ident = threading.get_ident() if ident is None else ident
        with self._cond:
            return self._write_depth if self._writer == ident else 0

    @property
    def is_write_locked(self) -> bool:
        with self._cond:
            return self._writer is not None

    def reader_count(self) -> int:
        with self._cond:
            return len(self._readers)

    def writers_waiting(self) -> int:
        with self._cond:
            return self._writers_waiting

    def _snapshot(self) -> tuple[dict[int, int], Optional[int], int]:
        with self._cond:
            return dict(self._readers), self._writer, self._write_depth

    # ---------- 获取 ----------

    def acquire_read(self, timeout: Optional[float] = None) -> bool:
        """获取读锁（可重入）。

        timeout=None 永久等待；正数为等待秒数；0 不等待。
        超时返回 False（timeout=0 时改为抛 AcquireTimeout，便于发现逻辑错误）。
        规则性错误（如状态非法）立即抛异常，不会进入等待。
        """
        me = threading.get_ident()
        with self._cond:
            # 规则 3：写者拿读锁，记账但不降级。
            if self._writer == me:
                self._readers[me] = self._readers.get(me, 0) + 1
                return True
            # 规则 2：读锁重入，无视等待写者直接放行。
            if me in self._readers:
                self._readers[me] += 1
                return True

            if not self._wait_for(
                lambda: self._writer is None and self._writers_waiting == 0,
                timeout,
            ):
                if timeout == 0:
                    raise AcquireTimeout("无法以非阻塞方式获得读锁")
                return False
            self._readers[me] = 1
            return True

    def acquire_write(self, timeout: Optional[float] = None) -> bool:
        """获取写锁（可重入）。

        读 -> 写升级：本线程为唯一读者时允许，否则立即抛 UpgradeError。
        """
        me = threading.get_ident()
        with self._cond:
            # 规则 2：写锁重入。
            if self._writer == me:
                self._write_depth += 1
                return True

            # 规则 4：读 -> 写升级。
            if me in self._readers:
                if len(self._readers) != 1:
                    raise UpgradeError(
                        "读 -> 写升级被拒绝：还有 %d 个其他读者在场，"
                        "等待会造成升级死锁；请先协调读者退出或改用显式协议"
                        % (len(self._readers) - 1)
                    )
                if self._writer is not None:
                    # 理论上不可达：有写者时读者表应为空，保留防御性断言。
                    raise RWLockError("内部状态错误：写锁与读锁同时被持有")
                # 唯一读者就地升级：读账保留（成为升级前的"读足迹"），
                # 由 release_write 归零后自然恢复为读者身份的处理见
                # downgrade()；这里删除读账，语义为纯升级。
                del self._readers[me]
                self._writer = me
                self._write_depth = 1
                return True

            if not self._wait_for(
                lambda: self._writer is None and not self._readers,
                timeout,
                as_writer=True,
            ):
                if timeout == 0:
                    raise AcquireTimeout("无法以非阻塞方式获得写锁")
                return False
            self._writer = me
            self._write_depth = 1
            return True

    # ---------- 释放 ----------

    def release_read(self) -> None:
        me = threading.get_ident()
        with self._cond:
            depth = self._readers.get(me, 0)
            if depth == 0:
                raise ReleaseUnheldError("当前线程未持有读锁，不能释放")
            depth -= 1
            if depth == 0:
                del self._readers[me]
                self._cond.notify_all()
            else:
                self._readers[me] = depth

    def release_write(self) -> None:
        me = threading.get_ident()
        with self._cond:
            if self._writer != me:
                raise ReleaseUnheldError("当前线程未持有写锁，不能释放")
            assert self._write_depth > 0, "写深度断言失败：持有者存在但深度<=0"
            self._write_depth -= 1
            if self._write_depth == 0:
                self._writer = None
                self._cond.notify_all()

    # ---------- 显式升降级 ----------

    def downgrade(self) -> None:
        """写 -> 读显式降级。

        要求写重入深度恰好为 1；成功后写锁真正释放（唤醒所有等待者），
        本线程转为持有一层读锁。
        """
        me = threading.get_ident()
        with self._cond:
            if self._writer != me:
                raise DowngradeError("当前线程未持有写锁，不能降级")
            if self._write_depth != 1:
                raise DowngradeError(
                    "写重入深度为 %d，必须先释放到 1 层写锁才允许降级"
                    % self._write_depth
                )
            self._writer = None
            self._write_depth = 0
            self._readers[me] = self._readers.get(me, 0) + 1
            self._cond.notify_all()

    # ---------- 上下文管理器 ----------

    @contextmanager
    def read_locked(self):
        self.acquire_read()
        try:
            yield
        finally:
            self.release_read()

    @contextmanager
    def write_locked(self):
        self.acquire_write()
        try:
            yield
        finally:
            self.release_write()

    # ---------- 内部工具 ----------

    def _wait_for(self, predicate, timeout, as_writer=False) -> bool:
        """在条件变量上等待 predicate 成立，统一处理 deadline 与写者计数。"""
        if timeout is not None and timeout < 0:
            raise ValueError("timeout 不能为负数")
        if as_writer:
            self._writers_waiting += 1
        try:
            if timeout is None:
                while not predicate():
                    self._cond.wait()
                return True
            if timeout == 0:
                return bool(predicate())
            end = time.monotonic() + timeout
            while not predicate():
                remaining = end - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)
            return True
        finally:
            if as_writer:
                self._writers_waiting -= 1
