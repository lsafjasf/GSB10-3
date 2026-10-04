# 失败归因聚类报告

- 失败用例总数: 37
- 聚类簇数: 7
- 关键失败（强制单独成簇）: 3

| 簇ID | 数量 | 关键 | 错误类型 | 失败位置 | 消息模板 | 代表用例 |
|---|---|---|---|---|---|---|
| C01 | 1 | ★ | DataCorruptionError | src/ledger.py::append | checksum mismatch at block <ADDR> | test_ledger_write_integrity |
| C02 | 1 | ★ | SecurityError | src/payment_service.py::refund | token validation bypassed for refund amount <N> | test_payment_refund_auth_bypass |
| C03 | 1 | ★ | ValueError | src/cart_service.py::parse_quantity | invalid literal for int() with base <N>: '<V>' | test_auth_token_expiry_parse |
| C04 | 22 |  | ValueError | src/cart_service.py::parse_quantity | invalid literal for int() with base <N>: '<V>' | test_cart_add_item_case_01 |
| C05 | 5 |  | KeyError | src/session_store.py::get_session | '<V>' | test_session_flow_01 |
| C06 | 4 |  | FileNotFoundError | src/config_loader.py::load_config | config file not found: app.yaml | test_config_load_env_01 |
| C07 | 3 |  | TimeoutError | src/http_client.py::send | request timed out after <N> ms | test_external_api_sync_01 |

## C01 [关键-隔离] — DataCorruptionError（1 条）

- 失败位置: `src/ledger.py::append`
- 消息模板: `checksum mismatch at block <ADDR>`
- 代表用例: `test_ledger_write_integrity` — checksum mismatch at block 0x7f3a21
- 隔离依据: R1:用例显式标注为关键失败；R2:错误类型属于关键错误集合(DataCorruptionError)
- 簇内用例: `test_ledger_write_integrity`

## C02 [关键-隔离] — SecurityError（1 条）

- 失败位置: `src/payment_service.py::refund`
- 消息模板: `token validation bypassed for refund amount <N>`
- 代表用例: `test_payment_refund_auth_bypass` — token validation bypassed for refund amount 9900
- 隔离依据: R2:错误类型属于关键错误集合(SecurityError)；R3:用例名/标签命中关键链路模式('payment')
- 簇内用例: `test_payment_refund_auth_bypass`

## C03 [关键-隔离] — ValueError（1 条）

- 失败位置: `src/cart_service.py::parse_quantity`
- 消息模板: `invalid literal for int() with base <N>: '<V>'`
- 代表用例: `test_auth_token_expiry_parse` — invalid literal for int() with base 10: 'exp'
- 隔离依据: R3:用例名/标签命中关键链路模式('auth')
- 簇内用例: `test_auth_token_expiry_parse`

## C04 — ValueError（22 条）

- 失败位置: `src/cart_service.py::parse_quantity`
- 消息模板: `invalid literal for int() with base <N>: '<V>'`
- 代表用例: `test_cart_add_item_case_01` — invalid literal for int() with base 10: '1.5.2'
- 簇内用例: `test_cart_add_item_case_01`, `test_cart_add_item_case_02`, `test_cart_add_item_case_03`, `test_cart_add_item_case_04`, `test_cart_add_item_case_05`, `test_cart_add_item_case_06`, `test_cart_add_item_case_07`, `test_cart_add_item_case_08`, `test_cart_add_item_case_09`, `test_cart_add_item_case_10`, `test_cart_add_item_case_11`, `test_cart_add_item_case_12`, `test_cart_add_item_case_13`, `test_cart_add_item_case_14`, `test_cart_add_item_case_15`, `test_cart_add_item_case_16`, `test_cart_add_item_case_17`, `test_cart_add_item_case_18`, `test_cart_add_item_case_19`, `test_cart_add_item_case_20`, `test_cart_add_item_case_21`, `test_cart_add_item_case_22`

## C05 — KeyError（5 条）

- 失败位置: `src/session_store.py::get_session`
- 消息模板: `'<V>'`
- 代表用例: `test_session_flow_01` — 'user_id'
- 簇内用例: `test_session_flow_01`, `test_session_flow_02`, `test_session_flow_03`, `test_session_flow_04`, `test_session_flow_05`

## C06 — FileNotFoundError（4 条）

- 失败位置: `src/config_loader.py::load_config`
- 消息模板: `config file not found: app.yaml`
- 代表用例: `test_config_load_env_01` — config file not found: app.yaml
- 簇内用例: `test_config_load_env_01`, `test_config_load_env_02`, `test_config_load_env_03`, `test_config_load_env_04`

## C07 — TimeoutError（3 条）

- 失败位置: `src/http_client.py::send`
- 消息模板: `request timed out after <N> ms`
- 代表用例: `test_external_api_sync_01` — request timed out after 5000 ms
- 簇内用例: `test_external_api_sync_01`, `test_external_api_sync_02`, `test_external_api_sync_03`
