# 约定清单与可执行断言对应表

`src/legacy_pipeline.py` 中靠注释和口口相传维持的约定，重构后全部落成
`src/pipeline.py` 中的可执行断言（违反时抛 `ContractViolation`，消息前缀
即约定编号）。共 9 条，按四类划分。

## 一、隐式约定清单（分类）

### 数据结构形状
- **C1** Job 必须是 dict，必含 `id`/`kind`/`payload`/`priority` 四键；
  `id` 为非空 str，`kind ∈ {email, report, cleanup}`，`payload` 为 dict。
- **C2** `run()` 返回的每个 result 是 dict，键恰好为
  `{job_id, status, attempts}`；`status ∈ {ok, failed}`，`attempts ≥ 1`。

### 调用顺序
- **C3** 生命周期 `new → open → closed`：`open()` 恰好一次且最先调用；
  `submit()/run()/close()` 只能在 open 之后、close 之前；不可重开、不可重复 close。
- **C4** `run()` 之前必须至少 `submit()` 一个 job。

### 取值范围
- **C5** `priority` 为整数（拒绝 bool），闭区间 `[0, 9]`，0 最紧急。
- **C6** 构造参数：`max_retries` 为整数 `[0, 5]`；`timeout ∈ (0, 300]` 秒
  （下开上闭）。
- **C7** 同一 Pipeline 会话内 job `id` 唯一。

### 线程约束
- **C8** 线程亲和：Pipeline 所有方法只能在调用 `open()` 的线程中使用。
- **C9** `run()` 不允许并发/重入（包括从 `on_job` 回调里再次调用）。

## 二、约定 ↔ 断言 ↔ 测试对应表

| 约定 | 断言位置（src/pipeline.py） | 通过用例 | 被破坏用例 |
|---|---|---|---|
| C1 | `validate_job()` | `test_pass_valid_job` 等 | `test_violation_missing_key`、`test_violation_wrong_types` |
| C2 | `validate_result()`（`run()` 后置条件） | `test_pass_run_results` | `test_violation_validate_result_rejects_bad_shapes` |
| C3 | `open()`/`_check_open()` | `test_pass_full_lifecycle` | `test_violation_submit_before_open`、`test_violation_run_before_open`、`test_violation_open_twice`、`test_violation_submit_after_close`、`test_violation_close_twice` |
| C4 | `run()` 中 `_require(len(self._jobs) > 0, ...)` | `test_pass_run_after_submit` | `test_violation_empty_run` |
| C5 | `validate_job()` 中 priority 检查 | `test_pass_priority_boundaries`（0、9） | `test_violation_priority_out_of_range`（-1、10、100）、`test_violation_priority_wrong_type`（5.0、True、"5"） |
| C6 | `Pipeline.__init__()` | `test_pass_constructor_boundaries` | `test_violation_retries_out_of_range`、`test_violation_timeout_out_of_range`、`test_violation_constructor_wrong_types` |
| C7 | `submit()` 中 `_seen_ids` 检查 | `test_pass_distinct_ids` | `test_violation_duplicate_id` |
| C8 | `_check_thread_affinity()` | `test_pass_used_entirely_in_worker_thread` | `test_violation_submit_from_other_thread`、`test_violation_close_from_other_thread` |
| C9 | `run()` 中 `_running` 守卫 | `test_pass_sequential_runs`、`test_violation_run_flag_still_cleared_after_error` | `test_violation_reentrant_run_from_callback` |

## 三、违规注入验证结果

`src/inject_violations.py` 对源码逐一注入违规改动（变异测试），再跑完整
回归套件。9 个变异全部被捕获，证明每条断言都是承重的：

```
CAUGHT  M1  C1 (删除 job 缺键检查):            套件失败 1 项
CAUGHT  M2  C2 (架空 result 键集合校验):        套件失败 1 项
CAUGHT  M3  C3 (submit 不再检查生命周期状态):    套件失败 2 项
CAUGHT  M4  C4 (允许空管线 run()):             套件失败 1 项
CAUGHT  M5  C5 (priority 越界不再报错):         套件失败 1 项
CAUGHT  M6  C6 (max_retries 越界不再报错):      套件失败 1 项
CAUGHT  M7  C7 (允许重复 job id):              套件失败 1 项
CAUGHT  M8  C8 (线程亲和检查失效):              套件失败 2 项
CAUGHT  M9  C9 (允许 run() 重入):              套件失败 2 项
通过：全部 9 个变异均被回归套件捕获，断言均为承重断言。
```

M7（删除重复 id 检查）注入后的失败输出样例：

```
............................F......
======================================================================
FAIL: test_violation_duplicate_id (test_pipeline_mutant.TestC7UniqueIds.test_violation_duplicate_id)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "src/test_pipeline.py", line 190, in test_violation_duplicate_id
    with self.assertRaises(ContractViolation) as ctx:
AssertionError: ContractViolation not raised
----------------------------------------------------------------------
Ran 35 tests in 0.002s
FAILED (failures=1)
```

## 四、边界用例

| 边界 | 通过 | 拒绝 |
|---|---|---|
| priority 闭区间 | 0、9 | -1、10、100、5.0、True、"5" |
| max_retries 闭区间 | 0、5 | -1、6、True、"3" |
| timeout 下开上闭 | 0.000001、300、300.0 | 0、-1.0、300.1、300.000001、True、"30" |
| job id | 非空字符串 | 空串 `""` |
| kind 大小写敏感 | `email` | `EMAIL`、`sms` |
| attempts 派生值 | retries=0 → 1；retries=5 → 6 | — |
| 生命周期 | new→open→(submit/run)*→close | open 两次、close 两次、close 后 submit/run |
| 线程 | 整个生命周期在同一工作线程 | 跨线程 submit/close |

## 五、运行方式

仅需 Python 3 标准库（开发环境为 3.12）：

```bash
cd src
python3 test_pipeline.py        # 回归测试：35 个用例，应全部 OK
python3 inject_violations.py    # 违规注入验证：9 个变异应全部 CAUGHT
```

退出码均为 0 表示通过；任何约定被破坏或断言失效都会以非零码退出。
