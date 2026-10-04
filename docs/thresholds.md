# 阈值收敛对照表

## 1. 重构前：全部常量的定义位置与取值（21 处 / 10 个文件）

由 `tools/inventory.py`（基于 `ast` 静态扫描）自动生成，行号即定义行：

| 常量 | 定义位置 | 取值 | 状态 |
| --- | --- | --- | --- |
| ALERT_THRESHOLD | legacy/alerting.py:3 | 0.95 | live |
| ALERT_THRESHOLD | legacy/report.py:4 | 0.95 | live |
| ALERT_THRESHOLD | legacy/settings.py:8 | 0.9 | dead |
| ALERT_THRESHOLD | legacy_tests/test_alert.py:6 | 0.95 | test-mirror |
| BATCH_SIZE | legacy/aggregator.py:3 | 100 | live |
| BATCH_SIZE | legacy/settings.py:6 | 100 | dead |
| BATCH_SIZE | legacy/worker.py:4 | 100 | live |
| CACHE_TTL | legacy/api.py:6 | 360 | dead |
| CACHE_TTL | legacy/cache.py:3 | 300 | live |
| CACHE_TTL | legacy/settings.py:5 | 300 | dead |
| FLUSH_INTERVAL | legacy/aggregator.py:4 | 60 | live |
| FLUSH_INTERVAL | legacy/alerting.py:4 | 60 | live |
| FLUSH_INTERVAL | legacy/settings.py:7 | 60 | dead |
| MAX_RETRIES | legacy/client.py:4 | 3 | live |
| MAX_RETRIES | legacy/worker.py:3 | 5 | dead |
| MAX_RETRIES | legacy/settings.py:4 | 3 | dead |
| REQUEST_TIMEOUT | legacy/api.py:5 | 30 | live |
| REQUEST_TIMEOUT | legacy/client.py:3 | 30 | live |
| REQUEST_TIMEOUT | legacy/report.py:3 | 30 | live |
| REQUEST_TIMEOUT | legacy/settings.py:3 | 30 | dead |
| WARMUP_ROUNDS | legacy_tests/test_pipeline.py:5 | 2 | test-only |

状态说明（引用关系由扫描器按模块作用域解析，非简单文本匹配）：

- `live`：定义在生产代码中且被同文件 Load 或跨文件 import 引用，取值真实参与对外行为。
- `dead`：定义后无任何引用（含整个文件无人导入的 `legacy/settings.py`）。
- `test-only`：只在测试中定义和使用，生产代码中不存在同名常量。
- `test-mirror`：测试文件里复制了一份生产常量，无任何同步保障。

## 2. 重构后：单一注册表

全部生产阈值收敛到 `app/thresholds.py`，以模块常量 + 只读 `REGISTRY`
（`types.MappingProxyType`）形式暴露；其余模块只允许 `from .thresholds import ...`，
不得再出现数值重定义（由 `tests/test_registry_sources.py` 静态校验）。

## 3. 前后取值对照

| 常量 | 重构前各副本取值（位置） | 最终采用值 | 冲突 |
| --- | --- | --- | --- |
| REQUEST_TIMEOUT | 30（client.py live）/ 30（api.py live）/ 30（report.py live）/ 30（settings.py dead） | **30** | 无，全部一致 |
| MAX_RETRIES | 3（client.py live）/ 5（worker.py dead）/ 3（settings.py dead） | **3** | 是 |
| CACHE_TTL | 300（cache.py live）/ 360（api.py dead）/ 300（settings.py dead） | **300** | 是 |
| BATCH_SIZE | 100（aggregator.py live）/ 100（worker.py live）/ 100（settings.py dead） | **100** | 无，全部一致 |
| FLUSH_INTERVAL | 60（aggregator.py live）/ 60（alerting.py live）/ 60（settings.py dead） | **60** | 无，全部一致 |
| ALERT_THRESHOLD | 0.95（alerting.py live）/ 0.95（report.py live）/ 0.9（settings.py dead）/ 0.95（测试 test-mirror） | **0.95** | 是 |
| WARMUP_ROUNDS | 2（仅 legacy_tests/test_pipeline.py） | **不进入注册表**，保留在测试本地 | 否（测试专用） |

## 4. 取值冲突清单、最终取值与理由

收敛原则（保证"对外行为零变化"）：**一律采用生效路径（live）上的取值；
dead 副本连同定义一起删除**。每个冲突都在 `app/thresholds.py` 的
`REGISTRY[name]["dropped"]` 元数据中留痕，并由
`tests/test_registry.py::AdoptionTest` 断言。

| 冲突常量 | 冲突取值 | 最终采用 | 理由 |
| --- | --- | --- | --- |
| MAX_RETRIES | 3 vs **5**（legacy/worker.py:3） | 3 | 真正的重试循环在 `client.fetch_with_retry`，读的是 client.py 的 3；worker.py 的 5 定义后从未被引用（worker 自己不做重试，也无人 `import` 它）。采用 5 会凭空增加 2 次重试，改变超时/失败语义。 |
| CACHE_TTL | 300 vs **360**（legacy/api.py:6） | 300 | 默认 TTL 只在 `cache.Cache.put(ttl=None)` 时生效，读的是 cache.py 的 300；api.py 的 360 从未被读取——处理器写缓存时显式传 `ttl=120`。采用 360 不会影响任何现有行为，只会留下错误的"文档值"。 |
| ALERT_THRESHOLD | 0.95 vs **0.90**（legacy/settings.py:8） | 0.95 | `settings.py` 整个文件无人导入，是漂移的"头"；告警判定实际走 `alerting.breached` / `report.grade`，用的是 0.95（"尾"）。这正是头尾不一致的取值；采用 0.90 会把 [0.90, 0.95) 区间的比率从非告警改成告警。 |

补充：`settings.py` 其余 5 个常量虽与生效值一致，但同样无任何引用，按 dead
处理、随文件删除，不再保留"第二处可写"。测试中的 `ALERT_THRESHOLD=0.95`
（test-mirror）删除后改为 `from app.thresholds import ALERT_THRESHOLD`，
杜绝测试与生产静默漂移；`WARMUP_ROUNDS=2`（test-only）是测试暖场脚手架，
与生产阈值无关，刻意保留在测试本地。

## 5. 要求的三种情形覆盖

| 情形 | 示例 | 处理方式 | 校验 |
| --- | --- | --- | --- |
| 取值相同 | REQUEST_TIMEOUT=30（3 个 live 文件）、BATCH_SIZE=100、FLUSH_INTERVAL=60 | 直接收敛到注册表，各模块改为 import | 差分别库 + 静态扫描 |
| 取值不同 | MAX_RETRIES 3/5、CACHE_TTL 300/360、ALERT_THRESHOLD 0.95/0.90 | 采用 live 值，删除 dead 值，冲突写入注册表元数据 | `AdoptionTest` 断言 live 值唯一且等于采用值、冲突逐笔登记 |
| 只在测试中使用 | WARMUP_ROUNDS=2 | 不进注册表，留在测试模块 | `test_test_only_constant_is_not_in_registry` |
| （附加）测试复制生产值 | 旧 test_alert.py 里的 ALERT_THRESHOLD=0.95 | 删除副本，改从注册表导入 | 重构后测试 `tests/test_alert.py` |
