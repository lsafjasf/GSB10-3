"""配置热加载库（仅标准库）。

设计要点：
1. 整体校验：新配置先完整解析 + 校验，全部通过后才切换；
   任何一项不合法都抛出 ConfigError（携带全部失败原因），旧配置原样保留。
2. 原子切换：当前配置是「单个不可变快照对象的引用」。读取方只做一次
   引用读取（CPython 中由 GIL 保证原子），写线程在锁内一次性替换该引用。
   因此读取方永远看到完整的旧版本或完整的新版本，绝不会看到半新半旧。
3. 可回滚：每次生效的快照都进入历史，可回滚到上一版或指定版本。
"""

from __future__ import annotations

import json
import threading
import time
from types import MappingProxyType

__all__ = [
    "ConfigError",
    "ConfigSnapshot",
    "HotConfig",
    "Schema",
    "Field",
    "load_json_file",
]


class ConfigError(Exception):
    """配置加载/校验失败。reasons 携带全部失败原因。"""

    def __init__(self, reasons):
        if isinstance(reasons, str):
            reasons = [reasons]
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


def _freeze(value):
    """深冻结：dict->只读映射，list->tuple，set->frozenset，其余原样返回。"""
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(v) for v in value)
    return value


class ConfigSnapshot:
    """一个完整、不可变的配置版本。读取方拿到的永远是这种整体快照。"""

    __slots__ = ("version", "data", "source", "loaded_at")

    def __init__(self, version, data, source, loaded_at):
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "data", _freeze(data))
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "loaded_at", loaded_at)

    def __setattr__(self, name, value):
        raise AttributeError("ConfigSnapshot 是不可变的")

    def get(self, key, default=None):
        return self.data.get(key, default)

    def __getitem__(self, key):
        return self.data[key]

    def __contains__(self, key):
        return key in self.data

    def __repr__(self):
        return f"ConfigSnapshot(version={self.version}, source={self.source!r})"


class Field:
    """单个配置项的校验规则。"""

    def __init__(self, name, types=None, required=True, default=None,
                 choices=None, min_value=None, max_value=None, check=None):
        self.name = name
        self.types = types  # type 或 type 元组；None 表示不限制
        self.required = required
        self.default = default
        self.choices = choices
        self.min_value = min_value
        self.max_value = max_value
        self.check = check  # check(value) -> None 或返回错误描述字符串

    def validate(self, data, errors):
        if self.name not in data:
            if self.required and self.default is None:
                errors.append(f"缺少必填项: {self.name}")
            return
        value = data[self.name]
        if self.types is not None and not isinstance(value, self.types):
            errors.append(
                f"项 {self.name} 类型错误: 期望 {self._type_names()}, "
                f"实际 {type(value).__name__}"
            )
            return  # 类型不对就不再继续做值校验，避免级联误报
        if self.choices is not None and value not in self.choices:
            errors.append(f"项 {self.name} 取值非法: {value!r} 不在 {list(self.choices)!r} 中")
        if self.min_value is not None and value < self.min_value:
            errors.append(f"项 {self.name} 过小: {value!r} < {self.min_value!r}")
        if self.max_value is not None and value > self.max_value:
            errors.append(f"项 {self.name} 过大: {value!r} > {self.max_value!r}")
        if self.check is not None:
            try:
                problem = self.check(value)
            except Exception as exc:  # 自定义校验自身出错也算不合法
                problem = f"自定义校验抛出异常: {exc!r}"
            if problem:
                errors.append(f"项 {self.name} 未通过自定义校验: {problem}")

    def _type_names(self):
        types = self.types if isinstance(self.types, tuple) else (self.types,)
        return "/".join(t.__name__ for t in types)


class Schema:
    """一组 Field 组成的整体校验规则。validate 返回全部错误（空列表表示通过）。"""

    def __init__(self, fields, allow_unknown=True):
        self.fields = list(fields)
        self.allow_unknown = allow_unknown

    def validate(self, data):
        errors = []
        if not isinstance(data, dict):
            return [f"配置顶层必须是对象(dict)，实际为 {type(data).__name__}"]
        for field in self.fields:
            field.validate(data, errors)
        if not self.allow_unknown:
            known = {f.name for f in self.fields}
            for key in data:
                if key not in known:
                    errors.append(f"未知配置项: {key}")
        return errors

    def apply_defaults(self, data):
        data = dict(data)
        for field in self.fields:
            if field.name not in data and field.default is not None:
                data[field.name] = field.default
        return data


def load_json_file(path):
    """读取并解析 JSON 文件；失败抛 ConfigError，不产生任何副作用。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise ConfigError(f"读取配置文件失败: {path}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件不是合法 JSON: {path}: {exc}") from exc


class HotConfig:
    """热加载配置持有者。

    读取：``hot.current`` / ``hot.get(key)``，无锁、原子，返回不可变快照。
    写入：``reload_from_dict`` / ``reload_from_file`` / ``rollback*``，
    由内部写锁串行化，且只有整体校验通过才会替换当前引用。
    """

    def __init__(self, schema=None, validator=None, initial=None, source="<initial>"):
        self._schema = schema
        self._validator = validator  # validator(data) -> None 或错误列表/抛 ConfigError
        self._lock = threading.Lock()
        self._current = None         # 读取方唯一访问点：单次引用读取即原子
        self._history = []           # 已生效快照（仅写线程在锁内访问）
        self._next_version = 1
        if initial is not None:
            self._swap(self._build(initial, source))

    # ---------- 读取路径（无锁、原子） ----------

    @property
    def current(self):
        """当前完整快照。单次引用读取，要么旧版本整体，要么新版本整体。"""
        snap = self._current
        if snap is None:
            raise ConfigError("尚未加载任何配置")
        return snap

    def get(self, key, default=None):
        return self.current.get(key, default)

    @property
    def version(self):
        return self.current.version

    @property
    def versions(self):
        """历史上所有生效过的版本号（含当前）。"""
        return [s.version for s in self._history]

    # ---------- 写入路径（写锁串行化） ----------

    def reload_from_dict(self, data, source="<dict>"):
        """整体校验通过才生效；失败抛 ConfigError，旧配置保持不变。"""
        with self._lock:
            snap = self._build(data, source)  # 校验失败在这里抛出，尚未触碰旧配置
            self._swap(snap)
            return snap

    def reload_from_file(self, path):
        data = load_json_file(path)  # 解析失败抛 ConfigError，旧配置保持不变
        return self.reload_from_dict(data, source=str(path))

    def rollback(self):
        """回滚到上一个生效版本。返回恢复后的快照。"""
        with self._lock:
            if len(self._history) < 2:
                raise ConfigError("没有可回滚的历史版本")
            self._history.pop()
            self._current = self._history[-1]
            return self._current

    def rollback_to(self, version):
        """回滚到指定版本（该版本必须在历史中）。返回恢复后的快照。"""
        with self._lock:
            for i in range(len(self._history) - 1, -1, -1):
                if self._history[i].version == version:
                    del self._history[i + 1:]
                    self._current = self._history[-1]
                    return self._current
            raise ConfigError(f"版本 {version} 不在历史 {self.versions} 中，无法回滚")

    # ---------- 内部 ----------

    def _build(self, data, source):
        """构造并整体校验新快照；任何一步失败都抛 ConfigError。"""
        errors = []
        if self._schema is not None:
            errors.extend(self._schema.validate(data))
        if not errors and isinstance(data, dict) and self._schema is not None:
            data = self._schema.apply_defaults(data)
        if not errors and self._validator is not None:
            try:
                result = self._validator(data)
            except ConfigError as exc:
                errors.extend(exc.reasons)
            except Exception as exc:
                errors.append(f"自定义校验器抛出异常: {exc!r}")
            else:
                if result:
                    errors.extend(result if isinstance(result, (list, tuple)) else [str(result)])
        if errors:
            raise ConfigError(errors)
        return ConfigSnapshot(self._next_version, data, source, time.time())

    def _swap(self, snap):
        """原子生效：单次引用替换。调用方必须持有写锁。"""
        self._history.append(snap)
        self._current = snap
        self._next_version = snap.version + 1
