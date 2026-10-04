# 代表用例清单（demo 回归）

共 33 条失败，6 个簇；
主要根因簇为 C1（规模最大的关键簇）。

| 簇 | 标签 | 数量 | 代表用例 | 失败位置 | 错误类型 | 原始信息 |
| --- | --- | --- | --- | --- | --- | --- |
| C1 | 关键 | 26 | `TC-001` | `src/payment/gateway.py:charge:88` | AssertionError | expected 200 got 500 (order 1001) |
| C2 | 关键 | 3 | `TC-027` | `src/inventory/service.py:deduct:55` | KeyError | 'sku' |
| C3 | 关键 | 1 | `TC-030` | `src/report/exporter.py:to_csv:34` | UnicodeDecodeError | 'gbk' codec can't decode byte 0xff in position 12: illegal multibyte sequence |
| C4 | 噪声 | 1 | `TC-031` | `tests/conftest.py:db_fixture:12` | RuntimeError | db container not ready after 30s |
| C5 | 噪声 | 1 | `TC-032` | `tests/conftest.py:redis_lock_fixture:58` | TimeoutError | failed to release redis lock: timed out after 10s |
| C6 | 噪声 | 1 | `TC-033` | `src/utils/http.py:post:40` | TimeoutError | request to http://10.0.0.8:8080/api/health timed out after 30s |
