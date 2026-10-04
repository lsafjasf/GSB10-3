# 阈值收敛：清单、对照表与冲突处理

## 1. 重构前全部常量的定义位置与取值

扫描范围：`legacy/`（11 个 Python 文件，24 处定义）。

| 文件 | 常量 | 取值 | 运行时状态 |
|---|---|---:|---|
| `api_client.py` | `REQUEST_TIMEOUT` | 30 | 活 |
| `api_client.py` | `MAX_RETRIES` | 3 | 活 |
| `api_client.py` | `RATE_LIMIT_PER_MINUTE` | 100 | 活 |
| `middleware.py` | `RATE_LIMIT_PER_MINUTE` | 100 | 活 |
| `middleware.py` | `REQUEST_TIMEOUT` | 30 | 活 |
| `worker.py` | `MAX_RETRIES` | 3 | 活 |
| `worker.py` | `JOB_TIMEOUT_SECONDS` | 300 | 活 |
| `scheduler.py` | `JOB_TIMEOUT_SECONDS` | 300 | 活 |
| `scheduler.py` | `MAX_BATCH_SIZE` | 500 | 活 |
| `exporter.py` | `MAX_BATCH_SIZE` | 1000 | 活 |
| `exporter.py` | `REQUEST_TIMEOUT` | 45 | 活 |
| `auth.py` | `SESSION_TIMEOUT_MINUTES` | 30 | 活（令牌校验唯一判定点） |
| `auth.py` | `MAX_LOGIN_ATTEMPTS` | 5 | 活 |
| `session.py` | `SESSION_TIMEOUT_MINUTES` | 60 | **死代码**（`grep` 验证全仓无引用） |
| `session.py` | `MAX_LOGIN_ATTEMPTS` | 5 | 活 |
| `pricing.py` | `MAX_DISCOUNT` | 0.30 | 活 |
| `pricing.py` | `BULK_THRESHOLD` | 20 | 活 |
| `cart.py` | `MAX_DISCOUNT` | 0.30 | 活 |
| `cart.py` | `BULK_THRESHOLD` | 20 | 活 |
| `notifier.py` | `SMS_LENGTH_LIMIT` | 160 | 活 |
| `notifier.py` | `MAX_RETRIES` | 3 | 活 |
| `report.py` | `REQUEST_TIMEOUT` | 30 | 活 |
| `report.py` | `MAX_BATCH_SIZE` | 500 | 活 |
| `test_helpers.py`（仅测试） | `SMS_LENGTH_LIMIT` | 160 | 测试，与生产重复 |
| `test_helpers.py`（仅测试） | `MAX_DISCOUNT` | 0.30 | 测试，与生产重复 |
| `test_helpers.py`（仅测试） | `FIXTURE_RATE_LIMIT` | 7 | 仅测试夹具，无生产对应物 |

按逻辑归并后为 10 个业务阈值 + 1 个纯测试夹具。

## 2. 取值冲突清单与最终采用值（理由）

### C1 `REQUEST_TIMEOUT`：30 vs 45（头尾不一致）

- 30 秒：`api_client.py`、`middleware.py`、`report.py`（普通 HTTP 调用）。
- 45 秒：`exporter.py`（导出大文件下载）。
- 两者均为活代码，但语义不同——导出下载天然需要更长预算。强行取任一值
  都会改变对外行为。
- **处理：拆分为两个条目**，取值原样保留：
  `HTTP_REQUEST_TIMEOUT_SECONDS = 30`、
  `EXPORT_REQUEST_TIMEOUT_SECONDS = 45`。

### C2 `MAX_BATCH_SIZE`：500 vs 1000

- 500：`scheduler.py`（入库分批）、`report.py`（报表分块）。
- 1000：`exporter.py`（导出分页）。
- 两者均为活代码，作用于不同流水线阶段。
- **处理：拆分为两个条目**：`INGEST_BATCH_SIZE = 500`、
  `EXPORT_BATCH_SIZE = 1000`。

### C3 `SESSION_TIMEOUT_MINUTES`：30 vs 60

- 30：`auth.py`，`session_valid()` 实际使用的唯一判定值。
- 60：`session.py`，令牌校验迁出后遗留；
  `grep -rn SESSION_TIMEOUT_MINUTES legacy/` 确认除定义本身外**零引用**。
- **处理：采用 30**（线上唯一活值），删除 60。该值从未参与执行，
  删除不改变任何对外行为，回归快照可证。

## 3. 前后取值对照表

| 重构前位置 | 重构前取值 | 重构后注册表条目 | 重构后取值 | 处理方式 |
|---|---:|---|---:|---|
| `api_client.REQUEST_TIMEOUT` | 30 | `HTTP_REQUEST_TIMEOUT_SECONDS` | 30 | 合并（值相同） |
| `middleware.REQUEST_TIMEOUT` | 30 | `HTTP_REQUEST_TIMEOUT_SECONDS` | 30 | 合并（值相同） |
| `report.REQUEST_TIMEOUT` | 30 | `HTTP_REQUEST_TIMEOUT_SECONDS` | 30 | 合并（值相同） |
| `exporter.REQUEST_TIMEOUT` | 45 | `EXPORT_REQUEST_TIMEOUT_SECONDS` | 45 | C1 拆分 |
| `api_client.MAX_RETRIES` | 3 | `MAX_RETRIES` | 3 | 合并（值相同） |
| `worker.MAX_RETRIES` | 3 | `MAX_RETRIES` | 3 | 合并（值相同） |
| `notifier.MAX_RETRIES` | 3 | `MAX_RETRIES` | 3 | 合并（值相同） |
| `api_client.RATE_LIMIT_PER_MINUTE` | 100 | `RATE_LIMIT_PER_MINUTE` | 100 | 合并（值相同） |
| `middleware.RATE_LIMIT_PER_MINUTE` | 100 | `RATE_LIMIT_PER_MINUTE` | 100 | 合并（值相同） |
| `worker.JOB_TIMEOUT_SECONDS` | 300 | `JOB_TIMEOUT_SECONDS` | 300 | 合并（值相同） |
| `scheduler.JOB_TIMEOUT_SECONDS` | 300 | `JOB_TIMEOUT_SECONDS` | 300 | 合并（值相同） |
| `scheduler.MAX_BATCH_SIZE` | 500 | `INGEST_BATCH_SIZE` | 500 | 合并（值相同） |
| `report.MAX_BATCH_SIZE` | 500 | `INGEST_BATCH_SIZE` | 500 | 合并（值相同） |
| `exporter.MAX_BATCH_SIZE` | 1000 | `EXPORT_BATCH_SIZE` | 1000 | C2 拆分 |
| `auth.SESSION_TIMEOUT_MINUTES` | 30 | `SESSION_TIMEOUT_MINUTES` | 30 | C3 采用活值 |
| `session.SESSION_TIMEOUT_MINUTES` | 60 | —（删除） | — | C3 死代码删除 |
| `auth.MAX_LOGIN_ATTEMPTS` | 5 | `MAX_LOGIN_ATTEMPTS` | 5 | 合并（值相同） |
| `session.MAX_LOGIN_ATTEMPTS` | 5 | `MAX_LOGIN_ATTEMPTS` | 5 | 合并（值相同） |
| `pricing.MAX_DISCOUNT` | 0.30 | `MAX_DISCOUNT` | 0.30 | 合并（值相同） |
| `cart.MAX_DISCOUNT` | 0.30 | `MAX_DISCOUNT` | 0.30 | 合并（值相同） |
| `test_helpers.MAX_DISCOUNT`（测试） | 0.30 | `MAX_DISCOUNT` | 0.30 | 测试改为引用注册表 |
| `pricing.BULK_THRESHOLD` | 20 | `BULK_THRESHOLD` | 20 | 合并（值相同） |
| `cart.BULK_THRESHOLD` | 20 | `BULK_THRESHOLD` | 20 | 合并（值相同） |
| `notifier.SMS_LENGTH_LIMIT` | 160 | `SMS_LENGTH_LIMIT` | 160 | 合并（值相同） |
| `test_helpers.SMS_LENGTH_LIMIT`（测试） | 160 | `SMS_LENGTH_LIMIT` | 160 | 测试改为引用注册表 |
| `test_helpers.FIXTURE_RATE_LIMIT`（仅测试） | 7 | 不进注册表（保留于 `app/test_helpers.py`） | 7 | 纯测试夹具 |

## 4. 三类要求覆盖情况

- **取值相同**：如 `MAX_RETRIES`（3 处均为 3）、`REQUEST_TIMEOUT`（3 处
  均为 30）、`RATE_LIMIT_PER_MINUTE`（2 处均为 100）等，直接合并为单一条目。
- **取值不同**：C1、C2 活代码冲突按语义拆分，C3 死代码冲突取活值，
  均不改变对外行为。
- **只在测试中使用的常量**：与生产重复的
  （`SMS_LENGTH_LIMIT`、`MAX_DISCOUNT`）改为测试直接引用注册表，
  杜绝测试夹具与生产取值漂移；纯夹具 `FIXTURE_RATE_LIMIT = 7`
  不是业务阈值，有意保留在 `app/test_helpers.py` 并在注册表守卫测试中
  显式白名单，避免被误当业务阈值收敛。

## 5. 防回潮机制

`tests/test_registry.py` 用 AST 扫描 `app/` 下所有模块，除注册表本身与
白名单夹具外，禁止再出现模块级全大写常量；注册表取值与本文件对照表由
测试逐一钉死。
