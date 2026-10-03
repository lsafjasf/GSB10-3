# 行式协议转义编解码（Python 3，仅标准库）

## 文件清单

| 文件 | 说明 |
| --- | --- |
| `escape_codec.py` | 编解码库：`encode_field/decode_field`、`encode_record/decode_record`、`encode_payload/decode_payload`、`EscapeError` |
| `test_escape_codec.py` | 边界用例与错误定位自测（unittest，18 例） |
| `fuzz_roundtrip.py` | 随机数据往返对拍 + 失败样例自动收敛（shrinking） |
| `error_demo.py` | 非法转义的行号/列号定位样例 |

## 格式约定

- 记录之间以 LF 分隔，字段之间以 `|` 分隔（可通过 `separator` 参数更换）。
- 编码器输出保证不含原始分隔符 / 换行 / 反斜杠：`\` → `\\`，`|` → `\|`，
  LF/CR/TAB/NUL → `\n` `\r` `\t` `\0`，其余控制字节 → `\xHH`。
- 解码器支持多种常见写法，按优先级（最长匹配优先）：
  `\xHH` > `\\` > `\n \r \t \0` > `\|`。
- 非法转义（未知字符、行末孤立反斜杠、`\x` 缺位/非法十六进制）抛出
  `EscapeError`，携带 1-based 行号与字节列号（列指向反斜杠），
  不猜测、不补全、不丢弃。

## 运行方式

```bash
python3 test_escape_codec.py -v          # 边界用例 + 错误定位自测
python3 fuzz_roundtrip.py                # 2 万轮随机往返对拍
python3 fuzz_roundtrip.py --seed 42 --trials 100000
python3 fuzz_roundtrip.py --demo-bug     # 注入缺陷解码器，演示失败收敛过程
python3 error_demo.py                    # 行号/列号定位样例
```

库的使用：

```python
from escape_codec import encode_payload, decode_payload
payload = encode_payload([[b"a|b", b"c\nd"], [b"", b"\\n"]])
assert decode_payload(payload) == [[b"a|b", b"c\nd"], [b"", b"\\n"]]
```

## 对拍与失败收敛

- `fuzz_roundtrip.py` 用随机种子生成任意字节（偏重 `\`、`|`、换行、
  形似转义的字母等危险字节），逐字节比较 `decode(encode(x)) == x`。
- 实测：`--seed 42 --trials 20000` 与 `--seed 20261003 --trials 100000`
  全部通过。
- 发现失败时自动做贪心 delta-debugging：删记录 → 删字段 → 字段砍半 →
  逐字节删除，每一步只接受仍然失败的更小输入，打印收敛过程并把最小
  失败样例写入 `failure_case.bin`。
- `--demo-bug` 演示（解码器把 `\n` 误当字面 `n`）：原始样例 2 条记录 /
  157 字节，11 步收敛到最小样例 `[[b'\x00']]`（编码为 `\0`，
  缺陷解码器无法还原）。

## 已覆盖边界

- 空字段 / 空记录 / 空负载、末尾 LF 终止符与中间空行的区分。
- 字段全是分隔符（`|||||` → `\|\|\|\|\|`，不会被误切成多字段）。
- 超长转义：1 万个连续反斜杠、10 万个分隔符、整段控制字节 `\xHH` 序列。
- 嵌套转义：字面量 `\n`、`\x41`、`\\n` 等多层嵌套往返无损；
  `\x5c`（反斜杠的十六进制写法）也能正确解码。
