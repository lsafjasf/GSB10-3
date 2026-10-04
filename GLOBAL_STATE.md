# 全局可变状态清单与残留检查

## 1. 旧版 `src/legacy_app.py` 全局状态清单

| 全局变量 | 声明位置 | 写位置 | 读位置 |
|---|---|---|---|
| `_current_request` | `src/legacy_app.py:7` | `set_request()` :16 | `get_request()` :25 |
| `_current_user` | `src/legacy_app.py:8` | `set_request()` :17 | `current_user()` :30 |
| `_request_id` | `src/legacy_app.py:9` | `set_request()` :18 | `get_request_id()` :35 |
| `g`（模块级 dict） | `src/legacy_app.py:10` | `g.clear()` :19、`g[...] = ...` :20、:46 | `g.get(...)` :50 |

隐式写：`set_request()` 内 `global` 声明 :15。
隐式读：`handle_request()` :43-47 通过 `current_user()` / `get_request_id()` / `g` 读取。

污染机理：N 个线程先各自执行 `set_request()` 覆写同一份模块变量，
然后都读到“最后一次写入”的请求数据；`g.clear()` 还会把其它线程刚写入的键删掉。
即使给写入加锁，也无法消除“写与读之间的交错窗口”——这是作用域错误，不是加锁问题。

## 2. 重构方式（`src/app.py`）

- 新增 `RequestContext`（`__slots__ = request/user/request_id/g`），每个请求 new 一份，`g` 是实例属性。
- `handle_request(ctx, ...)` 强制显式接收上下文；`_require_ctx()` 校验。
- 旧的 4 个全局访问器（`set_request`/`get_request`/`current_user`/`get_request_id`）保留名字但调用即抛 `RuntimeError`，引导迁移，防止有人继续依赖全局接口。
- 新增 `ContextMissingError`：缺上下文、类型错误、`request=None` 均显式报错。

## 3. 残留检查结果

静态检查（AST，测试 `NoResidualGlobalStateTest` 自动执行）：

- `global` 语句：0 处。
- 模块级可变容器字面量或 `dict()/list()/set()` 赋值：0 处。
- 模块级名字仅剩：类定义 `ContextMissingError` / `RequestContext`、
  纯函数 `handle_request` / `_require_ctx` / `_removed_global_api`、
  以及 4 个指向“调用即报错”闭包的旧接口名（不可变引用，不含任何状态）。

人工复核命令与结果：

```
$ grep -n '^\s*global ' src/app.py            # 无输出
$ grep -n '^[a-zA-Z_][a-zA-Z0-9_]*\s*=' src/app.py
set_request = _removed_global_api("set_request")
get_request = _removed_global_api("get_request")
current_user = _removed_global_api("current_user")
get_request_id = _removed_global_api("get_request_id")
```

以上 4 个赋值仅为旧 API 名称兼容，右侧是无状态闭包，不承载任何请求数据。
结论：重构后**不存在任何承载请求数据的隐式全局读写**。
