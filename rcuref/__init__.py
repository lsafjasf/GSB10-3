"""rcuref: 引用计数 + 宽限期延迟回收（仅标准库）。

典型用法见模块 README。公开接口：

- Domain / SharedObject / Ref / Registry
- ReclaimDomain（后台回收线程，可选）
- LifecycleError 及各子类
"""

from .core import (
    Domain,
    ReclaimDomain,
    SharedObject,
    Ref,
    LifecycleError,
    RefcountOverflowError,
    DoubleReleaseError,
    UseAfterReleaseError,
    ObjectReclaimedError,
    RetiredObjectError,
    DrainTimeoutError,
    DEFAULT_MAX_REFCOUNT,
)
from .registry import Registry, ReadLockRequiredError

__all__ = [
    "Domain",
    "ReclaimDomain",
    "SharedObject",
    "Ref",
    "Registry",
    "LifecycleError",
    "RefcountOverflowError",
    "DoubleReleaseError",
    "UseAfterReleaseError",
    "ObjectReclaimedError",
    "RetiredObjectError",
    "ReadLockRequiredError",
    "DrainTimeoutError",
    "DEFAULT_MAX_REFCOUNT",
]
