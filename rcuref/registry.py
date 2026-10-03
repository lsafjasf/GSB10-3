"""RCU 风格的读优化注册表。

- 读方：在 ``with domain.read_lock():`` 内 lookup，不加任何锁，
  拿到的对象在临界区内保证存活（回收被宽限期推迟）。
- 写方：publish/remove 只负责换指针与释放引用，旧对象进入待回收
  队列，等所有在读方退出后才真正销毁。
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

from .core import Domain, LifecycleError, SharedObject


class ReadLockRequiredError(LifecycleError):
    """在读临界区之外执行受保护的读操作。"""


class Registry:
    def __init__(self, domain: Domain) -> None:
        self._domain = domain
        self._lock = threading.Lock()
        self._table: Dict[Any, SharedObject] = {}

    @property
    def domain(self) -> Domain:
        return self._domain

    def publish(self, key: Any, obj: SharedObject) -> Optional[SharedObject]:
        """发布 key -> obj；若替换旧对象，释放其创建引用并返回旧对象。"""
        with self._lock:
            old = self._table.get(key)
            self._table[key] = obj
        if old is not None:
            old.release()
        return old

    def remove(self, key: Any) -> Optional[SharedObject]:
        """摘除并释放创建引用，返回被摘除的对象（不存在则 None）。"""
        with self._lock:
            old = self._table.pop(key, None)
        if old is not None:
            old.release()
        return old

    def lookup(self, key: Any) -> Optional[SharedObject]:
        """读方入口：必须在读临界区内调用。"""
        if not self._domain.in_read_section():
            raise ReadLockRequiredError(
                "lookup must be called inside 'with domain.read_lock()'"
            )
        with self._lock:
            return self._table.get(key)

    def remove_all(self) -> None:
        with self._lock:
            objs = list(self._table.values())
            self._table.clear()
        for obj in objs:
            obj.release()
