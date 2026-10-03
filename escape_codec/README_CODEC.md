# escape_codec —— 行式协议转义编解码

纯 Python 3 标准库实现，无任何第三方依赖。

## 协议

- 字段分隔符 `|`，记录分隔符 `\n`（一行一条记录），转义引导符 `\`
- 编码器输出**规范形式**：`\` → `\\`，`|` → `\|`，`\n` → `\n`，`\r` → `\r`，其余字节原样
  - 不变量：编码后的字段中绝不出现裸的 `|`、`\n`、`\r`、`\`
- 解码器**宽容输入**，按优先级匹配多种常见写法：
  1. `\xHH`（恰好 2 位 hex）
  2. `\uHHHH` / `\UHHHHHHHH`（Unicode 码点 → UTF-8 字节）
  3. `\0` ~ `\377`（1~3 位八进制，值 ≤ 255）
  4. 简单转义：`\\ \| \n \r \t \a \b \f \v \0 \' \"`
- 非法序列一律抛 `EscapeError`，携带**行号、列号（按字节计）、偏移与出错片段**；
  不猜测补全、不静默丢弃。

## 文件

| 文件 | 说明 |
|---|---|
| `escape.py` | 编解码库（`encode` / `decode` / `encode_field` / `decode_field` / `EscapeError`） |
| `tests/test_escape.py` | 单元测试：边界用例、多写法解码、优先级、错误定位 |
| `roundtrip_fuzz.py` | 随机往返对拍 + 独立参考解码器交叉验证 + 失败样例自动收缩 |
| `demo_errors.py` | 错误定位演示：解析 `examples/` 下的坏样本并打印行号/列号 |
| `examples/*.txt` | 错误定位样例输入 |
| `EDGE_CASES.md` | 边界用例清单 |

## 运行方式

```bash
cd escape_codec

# 单元测试（29 个用例）
python3 -m unittest discover -s tests -v

# 随机往返对拍（默认 20000 例；失败时自动收缩并落盘到 failure_cases/）
python3 roundtrip_fuzz.py --seed 20261003 --iterations 20000

# 演示失败样例的收敛过程（用假想坏解码器制造失败，再自动缩到最小）
python3 roundtrip_fuzz.py --demo-shrink

# 错误定位演示
python3 demo_errors.py
```

## 快速上手

```python
from escape import encode, decode, EscapeError

wire = encode([[b"a|b", b"line1\nline2"], [b""]])   # b'a\\|b|line1\\nline2\n\n'
assert decode(wire) == [[b"a|b", b"line1\nline2"], [b""]]

try:
    decode(b"ab\ncd\\qef")
except EscapeError as e:
    print(e.line, e.column, e.message)   # 2 3 unknown escape sequence: \'q\'
```
