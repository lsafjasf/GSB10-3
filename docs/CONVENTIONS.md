# 隐式约定清单（重构前）与可执行断言对应表

重构前这些约定只存在于 `legacy/legacy_processor.py` 的注释里；重构后
每条约定都有稳定编号、运行时断言、通过用例、违规用例和变异验证。

## 一、数据结构形状

| 编号 | 重构前的隐式约定（注释原文含义） | 可执行断言 | 断言位置 |
| --- | --- | --- | --- |
| S1 | event 必须恰好有 8 个固定键，值不能为 None（symbol 除外） | `frozenset(event.keys()) == REQUIRED_KEYS` 等 3 个 check | `src/events.py:17` |
| S2 | trade 必须带非空 symbol；quote 的 symbol 必须为 None | 按 type 校验 symbol 有无 | `src/events.py:59` |
| S3 | commit 返回的 receipt 必须含固定 5 字段且类型固定 | `_validate_receipt` | `src/processor.py:14` |

## 二、调用顺序

| 编号 | 隐式约定 | 可执行断言 | 断言位置 |
| --- | --- | --- | --- |
| O1 | 生命周期 open → close → commit → done，不能跳步/重复 | `_require_state`：`self._state in allowed` | `src/processor.py:54` |
| O2 | 同一批次内事件按 occurred_at 非递减入队 | 新事件时间戳 >= 上一个事件 | `src/processor.py:75` |
| O3 | on_commit 只能在 commit 之前注册 | state != done 且 callback 可调用 | `src/processor.py:60` |

## 三、取值范围

| 编号 | 隐式约定 | 可执行断言 | 断言位置 |
| --- | --- | --- | --- |
| R1 | event_id 必须是非空 str | `isinstance(str) and len > 0` | `src/events.py:26` |
| R2 | type 只能是 trade/quote | `event_type in EVENT_TYPES` | `src/events.py:32` |
| R3 | amount 必须是 int（非 bool/float），∈ [0, 10^9] | `type(amount) is int` + 区间 | `src/events.py:38` |
| R4 | occurred_at 是非负 int（Unix 毫秒），非 bool/float | `type(...) is int and >= 0` | `src/events.py:44` |
| R5 | tags 是 0..3 个 str 的 tuple，元素可哈希、不重复 | 两条 check | `src/events.py:50` |
| R6 | symbol 非空时必须 ∈ {AAPL, GOOG, TSLA} | `symbol in ALLOWED_SYMBOLS` | `src/events.py:68` |
| R7 | 单批次最多 1000 个事件 | `len(events) < max_batch` | `src/processor.py:80` |

## 四、线程约束

| 编号 | 隐式约定 | 可执行断言 | 断言位置 |
| --- | --- | --- | --- |
| T1 | 实例只能在创建线程使用（线程亲和） | `threading.get_ident() == self._owner` | `src/processor.py:48` |
| T2 | 外部调用（sink/回调）必须在锁外，防重入死锁 | `not self._lock._is_owned()` | `src/processor.py:113` |
| T3 | `*_locked` 内部方法只能在持锁时调用 | `self._lock._is_owned()` | `src/processor.py:97` |

## 断言实现方式

- 所有断言走唯一入口 `src/contracts.py` 的 `check(condition, convention_id, message)`，
  失败抛 `ContractViolation(AssertionError)`，信息带编号，如 `[R3] amount 必须是 ...`。
- 断言位于数据入口（`validate_event`）、公共方法入口（状态/线程检查）和锁边界，
  违规不可能绕过。
