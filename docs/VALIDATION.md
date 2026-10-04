# 校验规则说明

校验在边界处一次性完成（`src/validation.py`），任何非法输入都会在进入核心计价逻辑
之前被拒绝。所有错误收集在 `Err([Problem, ...])` 中，一条输入可以返回多条问题，
每条问题包含三个字段：

- `stage`：固定为 `"validation"`（与第三方失败 `"pricing"` 区分）
- `path`：精确定位，如 `orders[2].items[0].qty`；顶层为 `$`
- `reason`：人类可读原因（中文）

## 输入结构

```json
{
  "orders": [
    {
      "order_id": "A-1001",
      "items": [
        {"sku": "SKU-1", "qty": 2, "unit_price": 9.99}
      ],
      "coupon": "SAVE10"
    }
  ]
}
```

## 规则表

| 位置 | 规则 | 拒绝原因示例 |
| --- | --- | --- |
| `$` | 必须是 JSON 对象 | 顶层必须是对象，实际为 list |
| `$` | 只允许字段 `orders`（无多余字段） | 未知字段 'foo' |
| `$.orders` | 必填；必须是非空数组 | 缺少必填字段 / 必须是数组 |
| `orders[i]` | 必须是对象；只允许 `order_id` / `items` / `coupon` | 未知字段 'discount_code' |
| `orders[i].order_id` | 必填；字符串；`^[A-Za-z0-9-]{1,32}$` | 只允许字母/数字/连字符，长度 1-32 |
| `orders[i].items` | 必填；非空数组；每个元素是对象，只允许 `sku` / `qty` / `unit_price` | 至少包含一个条目 |
| `orders[i].items[j].sku` | 必填；非空字符串 | 必须是非空字符串 |
| `orders[i].items[j].qty` | 必填；正整数；**`bool` 拒绝**（`True` 不是合法整数） | 必须是正整数 / bool 不是合法的数值类型 |
| `orders[i].items[j].unit_price` | 必填；数值且 `>= 0`；**`bool` 拒绝** | 必须 >= 0 |
| `orders[i].coupon` | 可选；缺省或 `null` 表示无券；否则必须是字符串 | 必须是字符串或 null |

## 关键设计点

1. **整体拒绝**：`run_batch` 先校验整批输入，存在任意问题即抛 `InputRejected`，
   不处理任何订单、不写输出文件。
2. **只校验本模块边界**：优惠券是否真实存在（`SAVE10` 等）属于第三方模块的业务
   规则，边界不校验；它产生的异常由失败隔离层处理（见 README「失败隔离」）。
3. **只读**：校验不修改输入对象（有回归测试保证）。
