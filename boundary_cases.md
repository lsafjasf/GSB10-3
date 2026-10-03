# 边界判据与边界用例

## 合并判据（唯一权威定义）

同一五元组 `(proto, src_ip, src_port, dst_ip, dst_port)` 的记录，按
`(start_ts, end_ts, 到达序号)` 排序后，相邻两条记录：

```
gap = next.start_ts - prev.end_ts
gap <= idle_timeout   -> 同一会话（合并）
gap >  idle_timeout   -> 切分为新会话
```

- **边界值**：`gap == idle_timeout` **合并**；只有严格大于才切分。
- `gap <= 0`（时间重叠，或乱序到达导致的重叠）必然合并。
- 会话字段：`start = min(start_ts)`，`end = max(end_ts)`，
  字节数/包数为各记录之和，与逐条求和一致（有单元测试与对拍断言）。

## 老化判据

- 空闲老化（`sweep(now)`）：`last_seen < now - idle_timeout` 过期；
  `last_seen == now - idle_timeout` **保留**（与合并边界一致）。
- 容量老化：`max_active_sessions` 限制活跃五元组数，超出时按全局 LRU
  （最近一次有记录到达的时间）驱逐最旧的 key，其会话作为最终结果返回。
- 时间可注入：构造参数 `clock=` 或 `sweep(now=)`；测试不依赖真实时钟。

## 边界用例清单（均有对应测试 / 对拍数据）

| # | 用例 | 输入 | 期望 | 覆盖位置 |
|---|------|------|------|----------|
| 1 | 单条记录 | 1 条记录 | 1 个会话，计数原样 | `test_single_record` |
| 2 | gap == idle_timeout | end=5, 下条 start=15, timeout=10 | 合并为 1 个会话 | `test_gap_at_exact_boundary_merges` |
| 3 | gap 略大于 idle_timeout | end=5, 下条 start=15.000001 | 切为 2 个会话 | `test_gap_beyond_boundary_splits` |
| 4 | 五元组超时后复用 | end=5, 下条 start=100 | 新会话、新 session_id | `test_same_five_tuple_reused_after_timeout` |
| 5 | 乱序：迟到记录并入早会话 | (0,5),(30,35) 后到达 (8,12) | 第一个会话扩为 (0,12) | `test_late_record_merges_into_open_session` |
| 6 | 乱序：迟到长记录桥接两个会话 | (0,5),(20,25) 后到达 (4,21) | 三者融合为 1 个会话 | `test_late_record_bridges_two_sessions` |
| 7 | 完全逆序到达 | (20,25),(10,15),(0,5) | 合并为 1 个会话 (0,25) | `test_fully_reversed_arrival` |
| 8 | 老化边界 | last_seen == now - timeout | 保留；再小一点才过期 | `test_sweep_boundary_criterion` |
| 9 | 零时长记录同时间戳 | 3 条 (5,5) | 合并为 1 个会话 | 对拍数据 `zero_duration_same_ts` |
| 10 | idle_timeout = 0 | 任何正 gap | 全部切分 | 对拍数据 `zero_timeout` |
| 11 | LRU 容量上界 | cap=3 灌入 10 个 key | 活跃 key 数恒 ≤ 3 | `test_lru_eviction_bounds_active_keys` |
| 12 | 乱序 vs 排序等价 | 随机乱序 × 1200 组 | 与排序参考实现完全一致 | `differential_test.py` |
