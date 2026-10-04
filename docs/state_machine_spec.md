# 订单履约状态机业务文档

> 最后更新：2019-05-12（订单中心 v2.3）
> 状态机实现：`legacy/legacy_order_machine.py`，初始状态 `CREATED`

## 一、状态清单

| 状态 | 含义 |
| --- | --- |
| `CREATED` | 已下单，未支付 |
| `PAID` | 已支付，等待仓库 |
| `PICKING` | 仓库拣货中 |
| `PACKED` | 已打包，待发货 |
| `SHIPPED` | 已发货，在途 |
| `DELIVERED` | 已签收 |
| `CLOSED` | 订单关闭（终态） |
| `CANCELLED` | 订单取消（终态） |
| `AUDITING` | 风控人工审核中 |
| `REFUNDING` | 退款审批中 |
| `REFUND_APPROVED` | 退款已审批，等待打款 |
| `REFUNDED` | 退款完成（终态） |

## 二、状态转移表

| 当前状态 | 事件 | 下一状态 | 说明 |
| --- | --- | --- | --- |
| `CREATED` | `pay` | `PAID` | 支付成功 |
| `CREATED` | `cancel` | `CANCELLED` | 用户取消未支付订单 |
| `CREATED` | `timeout` | `CANCELLED` | 超时未支付自动取消 |
| `CREATED` | `flag_risk` | `AUDITING` | 命中风控规则，转人工审核 |
| `PAID` | `stock_ok` | `PICKING` | 库存确认，开始拣货 |
| `PAID` | `stock_out` | `PAID` | 缺货等待补货，状态不变 |
| `PAID` | `cancel` | `CANCELLED` | 用户取消 |
| `PICKING` | `pick_done` | `PACKED` | 拣货完成 |
| `PICKING` | `cancel` | `CANCELLED` | 用户取消 |
| `PACKED` | `ship` | `SHIPPED` | 出库发货 |
| `PACKED` | `cancel` | `CANCELLED` | 用户取消 |
| `SHIPPED` | `deliver` | `DELIVERED` | 物流签收 |
| `SHIPPED` | `timeout` | `DELIVERED` | 物流轨迹超时，系统自动签收 |
| `SHIPPED` | `cancel` | `（报错）` | 已发货不允许取消 |
| `DELIVERED` | `close` | `CLOSED` | 用户确认，订单关闭 |
| `DELIVERED` | `return_request` | `REFUNDING` | 用户发起退货 |
| `REFUNDING` | `refund_approve` | `REFUND_APPROVED` | 客服审批通过 |
| `REFUND_APPROVED` | `refund_done` | `REFUNDED` | 打款完成 |
| `AUDITING` | `audit_pass` | `CLOSED` | 风控审核通过 |
| `AUDITING` | `audit_fail` | `CANCELLED` | 风控审核不通过 |

## 三、非法事件处理约定

在**任何状态**下，收到上表未列出的事件，状态机一律抛出
`InvalidTransitionError`，不会发生状态变化。事件名拼错或使用了
系统未定义的事件，抛出 `UnknownEventError`。

## 四、终态

`CLOSED`、`CANCELLED`、`REFUNDED` 为终态，订单到达终态后流程结束。
