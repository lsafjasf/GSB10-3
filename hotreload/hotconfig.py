"""配置热加载核心库（Python 3 标准库实现）。

设计要点：
- 整体校验：新配置必须整体通过 validate_config，任何一项不合法都拒绝，
  旧配置原样保留，错误以 {字段路径: 原因} 的形式返回。
- 原子切换：每个版本是一个不可变的 ConfigVersion 快照；切换只是对
  _current 的一次引用赋值（写锁保护，读者无锁），读取方永远拿到完整的
  某一个版本，不存在半新半旧的中间态。
- 回滚：生效/失效都进入撤销栈，rollback()/redo() 可来回切换，同样是
  一次原子赋值。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional, Tuple

__all__ = [
    "ValidationError",
    "ConfigVersion",
    "ConfigStore",
    "JsonFileWatcher",
    "validate_config",
]

# ---------------------------------------------------------------------------
# 整体校验
# ---------------------------------------------------------------------------

_ALLOWED_KEYS = ("service_name", "port", "timeout_ms", "features", "rate_limit")
_ALLOWED_FEATURES = ("read", "write", "admin")
_MAX_TIMEOUT_MS = 60_000


class ValidationError(ValueError):
    """整体校验失败。errors 为 {字段路径: 原因}，一次性列出全部问题。"""

    def __init__(self, errors: Mapping[str, str]):
        self.errors = dict(errors)
        detail = "; ".join(f"{path}: {why}" for path, why in sorted(self.errors.items()))
        super().__init__(f"config validation failed ({len(self.errors)} error(s)): {detail}")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_config(raw: Any) -> Mapping[str, Any]:
    """整体校验一份原始配置，返回规范化后的配置（MappingProxyType 视图）。

    规则：全部字段必填；未知字段拒绝；类型与取值范围逐项检查；
    所有错误收集完毕后一次性抛出 ValidationError，绝不部分生效。
    """
    errors: dict[str, str] = {}

    if not isinstance(raw, Mapping):
        raise ValidationError({"<root>": f"config must be a mapping, got {type(raw).__name__}"})

    unknown = sorted(set(raw) - set(_ALLOWED_KEYS))
    for key in unknown:
        errors[key] = "unknown key"

    for key in _ALLOWED_KEYS:
        if key not in raw:
            errors[key] = "missing required key"

    def present(key: str) -> bool:
        return key in raw and key not in unknown

    if present("service_name"):
        v = raw["service_name"]
        if not isinstance(v, str) or not v.strip():
            errors["service_name"] = "must be a non-empty string"

    if present("port"):
        v = raw["port"]
        if not _is_int(v):
            errors["port"] = "must be an integer"
        elif not 1 <= v <= 65535:
            errors["port"] = "must be in [1, 65535]"

    if present("timeout_ms"):
        v = raw["timeout_ms"]
        if not _is_int(v):
            errors["timeout_ms"] = "must be an integer"
        elif not 1 <= v <= _MAX_TIMEOUT_MS:
            errors["timeout_ms"] = f"must be in [1, {_MAX_TIMEOUT_MS}]"

    if present("features"):
        v = raw["features"]
        if not isinstance(v, (list, tuple)) or isinstance(v, str):
            errors["features"] = "must be a list of strings"
        else:
            seen = set()
            for i, item in enumerate(v):
                if not isinstance(item, str):
                    errors[f"features[{i}]"] = "must be a string"
                elif item not in _ALLOWED_FEATURES:
                    errors[f"features[{i}]"] = f"unknown feature {item!r}"
                elif item in seen:
                    errors[f"features[{i}]"] = f"duplicate feature {item!r}"
                else:
                    seen.add(item)

    if present("rate_limit"):
        v = raw["rate_limit"]
        if not isinstance(v, Mapping):
            errors["rate_limit"] = "must be a mapping"
        else:
            extra = sorted(set(v) - {"qps", "burst"})
            for key in extra:
                errors[f"rate_limit.{key}"] = "unknown key"
            for key in ("qps", "burst"):
                if key not in v:
                    errors[f"rate_limit.{key}"] = "missing required key"
                elif not _is_int(v[key]):
                    errors[f"rate_limit.{key}"] = "must be an integer"
                elif v[key] < 1:
                    errors[f"rate_limit.{key}"] = "must be >= 1"

    if errors:
        raise ValidationError(errors)

    return MappingProxyType(
        {
            "service_name": raw["service_name"],
            "port": raw["port"],
            "timeout_ms": raw["timeout_ms"],
            "features": tuple(raw["features"]),
            "rate_limit": MappingProxyType(
                {"qps": raw["rate_limit"]["qps"], "burst": raw["rate_limit"]["burst"]}
            ),
        }
    )


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


# ---------------------------------------------------------------------------
# 不可变版本快照
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigVersion:
    """一个完整、不可变的配置版本。读取方拿到的永远是这种整体快照。"""

    version: int
    data: Mapping[str, Any]
    loaded_at: float
    source: str = "manual"

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


# ---------------------------------------------------------------------------
# 原子存储：切换 / 回滚 / 重做
# ---------------------------------------------------------------------------


class ConfigStore:
    """线程安全的配置存储。

    - load(raw)        整体校验通过后原子生效；失败抛 ValidationError，旧配置不变
    - rollback()       回到上一个版本（撤销一次生效）
    - redo()           重新应用被回滚的版本（撤销一次失效）
    - current          当前版本快照（一次无锁引用读取，永远完整）
    """

    def __init__(self, initial: Any, *, source: str = "initial"):
        data = validate_config(initial)  # 初始配置也必须整体合法，否则直接失败
        self._current = ConfigVersion(version=1, data=data, loaded_at=time.time(), source=source)
        self._next_version = 2  # 版本号跨回滚单调递增，永不复用
        self._write_lock = threading.Lock()
        self._undo: list[ConfigVersion] = []
        self._redo: list[ConfigVersion] = []

    # -- 读取（无锁，单次引用赋值保证原子） --------------------------------

    @property
    def current(self) -> ConfigVersion:
        return self._current

    def get(self, key: str, default: Any = None) -> Any:
        return self._current.data.get(key, default)

    # -- 写入 -------------------------------------------------------------

    def _before_commit(self, snapshot: ConfigVersion) -> None:
        """提交前的钩子（在写锁内、引用赋值之前调用）。仅供测试注入。"""

    def _commit(self, snapshot: ConfigVersion) -> None:
        # 整个切换就是这一次引用赋值：读者要么看到旧快照，要么看到新快照。
        self._before_commit(snapshot)
        self._current = snapshot

    def load(self, raw: Any, *, source: str = "manual") -> ConfigVersion:
        """整体校验 + 原子生效。校验失败抛 ValidationError，当前配置不变。"""
        data = validate_config(raw)  # 先整体校验，通过前不产生任何副作用
        with self._write_lock:
            snapshot = ConfigVersion(
                version=self._next_version,
                data=data,
                loaded_at=time.time(),
                source=source,
            )
            self._undo.append(self._current)
            self._redo.clear()  # 新的生效使“重做”历史失效
            self._next_version += 1
            self._commit(snapshot)
            return snapshot

    def load_json(self, text: str, *, source: str = "json") -> ConfigVersion:
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValidationError({"<json>": f"invalid JSON: {exc}"}) from exc
        return self.load(raw, source=source)

    def rollback(self) -> ConfigVersion:
        """回滚到上一个版本。没有历史时返回 None，当前配置不变。"""
        with self._write_lock:
            if not self._undo:
                return None
            target = self._undo.pop()
            self._redo.append(self._current)
            self._commit(target)
            return target

    def redo(self) -> ConfigVersion:
        """重做被回滚的版本。没有可重做项时返回 None。"""
        with self._write_lock:
            if not self._redo:
                return None
            target = self._redo.pop()
            self._undo.append(self._current)
            self._commit(target)
            return target

    # -- 观测 -------------------------------------------------------------

    @property
    def can_rollback(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def history(self) -> Tuple[int, ...]:
        """(undo 栈从旧到新, 当前, redo 栈) 的版本号，便于测试断言。"""
        with self._write_lock:
            return tuple(v.version for v in self._undo) + (self._current.version,) + tuple(
                v.version for v in reversed(self._redo)
            )


# ---------------------------------------------------------------------------
# JSON 文件监听热加载
# ---------------------------------------------------------------------------


class JsonFileWatcher:
    """轮询 JSON 配置文件，变更时整体校验并热加载。

    加载失败（JSON 语法错误 / 校验不通过）时旧配置保留，错误交给
    on_error 回调（默认记录到 self.last_error），线程继续运行。
    """

    def __init__(
        self,
        store: ConfigStore,
        path: str,
        *,
        interval: float = 0.5,
        on_error: Optional[Callable[[Exception], None]] = None,
    ):
        self.store = store
        self.path = path
        self.interval = interval
        self._on_error = on_error
        self.last_error: Optional[Exception] = None
        self.reload_count = 0
        self._digest: Optional[str] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._sync_state()

    def _read_bytes(self) -> bytes:
        with open(self.path, "rb") as fh:
            return fh.read()

    def _sync_state(self) -> None:
        try:
            self._digest = hashlib.sha256(self._read_bytes()).hexdigest()
        except OSError:
            self._digest = None

    def reload_now(self) -> bool:
        """立即检查并加载；返回是否发生了生效切换。失败不抛异常。"""
        try:
            content = self._read_bytes()
        except OSError as exc:
            self._report(exc)
            return False
        digest = hashlib.sha256(content).hexdigest()
        if digest == self._digest:
            return False
        try:
            self.store.load_json(content.decode("utf-8"), source=f"file:{self.path}")
        except (ValidationError, UnicodeDecodeError) as exc:
            self._report(exc)
            return False
        self._digest = digest
        self.last_error = None
        self.reload_count += 1
        return True

    def _report(self, exc: Exception) -> None:
        self.last_error = exc
        if self._on_error is not None:
            self._on_error(exc)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="config-watcher", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.reload_now()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "JsonFileWatcher":
        self.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.stop()
