# 全局可变状态清单与重构报告

## 1. 全局可变状态清单（重构前 `legacy_app.py`）

| 全局变量 | 写入位置 | 读取位置 | 危害 |
|---|---|---|---|
| `_current_request` | `set_request()`（`handle_request` 开头调用） | `get_current_request()`、`audit()`、`handle_request()` 返回结果时 | 并发请求互相覆盖对方身份，返回结果张冠李戴 |
| `_audit_log` | `audit()` 追加 | `get_audit_log()` | 所有请求共享一份日志，条目被归到错误的 request_id；且从不清理，测试之间互相泄漏 |
| `_discount_rate` | `set_discount()`（`handle_request` 开头调用） | `handle_request()` 计算总价时 | 后到的请求篡改先执行请求的折扣率，金额算错 |
| `_inter_request_hook` | 测试脚本赋值 | `_run_hook()` | 仅测试钩子（用于确定性复现并发交错），同属模块级可变状态 |

读写点汇总：`set_request`、`get_current_request`、`set_discount`、`audit`、`get_audit_log`、`handle_request`、`_run_hook`。

## 2. 重构方式

新模块 `app.py`：

- 新增 `RequestContext`（dataclass）：`request_id` / `user_id` / `discount` / `audit_log` 全部挂在实例上。
- `handle_request(ctx, items)`、`add_item(ctx, name, price)` 显式接收 ctx；`_require_context()` 对 `None` 或错误类型抛 `MissingContextError`。
- 旧签名由 `handle_request_legacy(request_id, user_id, items, discount=0.0)` 兼容：内部新建私有 context 再委托，发 `DeprecationWarning`，不碰任何全局状态。
- `legacy_app.py` 保留为只读参考（标记 DEPRECATED），供对比脚本与兼容测试使用。

## 3. 残留检查结果

```
$ grep -n "global " app.py
32:  "...no implicit global "        # 仅为错误消息字符串
64:  "so it never touches global state."  # 仅为 docstring
$ grep -nE "^[a-zA-Z_][a-zA-Z0-9_]* *=" app.py
(无输出)   # 模块级零可变赋值，只有 import / class / def
```

结论：`app.py` 中不存在任何 `global` 语句和模块级可变状态，隐式读写已清零。

## 4. 并发对比结果（`python3 scripts/concurrency_compare.py`）

用 Barrier 强制两个请求都先"安装上下文"再"消费上下文"，确定性复现交错：

```
== legacy (module-global context) ==
  result: request_id=req-B user_id=bob total=2.50 (expected 60.00) MISMATCH
  result: request_id=req-B user_id=bob total=60.00 (expected 60.00) OK
  audit entries attributed by global context: {'req-A': 3, 'req-B': 5}
  total mismatches: 1

== refactored (explicit RequestContext) ==
  result: request_id=req-A user_id=alice total=5.00 (expected 5.00) OK
  result: request_id=req-B user_id=bob total=60.00 (expected 60.00) OK
  audit entries attributed by global context: {'req-B': 4, 'req-A': 4}
  total mismatches: 0
```

- legacy：req-A 的结果被 req-B 的上下文污染（request_id 变成 req-B，折扣用了对方的 0.5，5.00 被算成 2.50）；审计日志 8 条中 3 条归错请求。
- 重构后：两个请求结果与各自审计日志完全隔离，0 处不一致。

测试并行性：4 个进程同时跑 `python3 -m unittest tests.test_app`，全部 OK（重构前共享 `_audit_log` 会让并行测试互相污染）。

## 5. 边界用例覆盖（`tests/test_app.py`，8 个用例）

| 情形 | 用例 |
|---|---|
| 单请求 | `test_single_request_result_and_audit`、`test_default_discount_is_zero` |
| 并发请求 | `test_concurrent_requests_do_not_pollute_each_other`（16 线程 + Barrier） |
| 上下文缺失 | `test_handle_request_without_context_raises`、`test_add_item_without_context_raises`、`test_wrong_type_is_rejected` |
| 旧接口被调用 | `test_legacy_shim_warns_and_still_works`（断言 DeprecationWarning + 结果正确）、`test_legacy_module_still_importable_for_reference` |

## 6. 运行方式

```bash
cd /home/administrator/gsb/uid233/B
python3 -m unittest tests.test_app -v        # 回归测试（8 用例）
python3 scripts/concurrency_compare.py       # 并发前后对比（退出码 0 = 重构后无污染）
```

仅依赖 Python 3 标准库（`dataclasses` / `threading` / `unittest` / `warnings`）。
