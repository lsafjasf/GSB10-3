# sensitive_container — 敏感数据容器（Python 3，仅标准库）

敏感缓冲区的安全容器：用完必须显式销毁，销毁即逐字节清零；销毁幂等；
使用期外的意外复制可被扫描检测并告警。

## 文件

| 文件 | 说明 |
|---|---|
| `sensitive_data.py` | 库：`SecretBuffer`（容器）+ `LeakScanner`（副本扫描器） |
| `test_sensitive_data.py` | 单元测试（19 个用例，unittest） |
| `selftest.py` | 自测演示：生成清零验证数据、幂等断言、拷贝计数与告警 |
| `verification_report.txt` | `selftest.py` 的运行产物（验证数据） |

## 运行方式

```bash
cd sensitive_container
python3 selftest.py                          # 演示 + 生成 verification_report.txt
python3 -m unittest test_sensitive_data -v   # 完整单元测试
```

要求：Python 3.10+（在 CPython 3.12 上验证），无第三方依赖。

## 用法

```python
from sensitive_data import SecretBuffer

secret = SecretBuffer(b"top-secret")
with secret.access() as view:   # 唯一的访问入口，返回 memoryview
    use(view)                   # 读写都在原缓冲区上进行，不产生副本
secret.destroy()                # 逐字节覆写为 0
secret.destroy()                # 幂等：不报错、不改变结果
secret.access()                 # 抛 SecretDestroyedError
```

- 使用中销毁默认被拒绝（`SecretInUseError`），提前销毁用 `destroy(force=True)`。
- `secret.audit()` 扫描解释器内存，返回 `LeakReport`（`copy_count` / `findings`），
  统计容器之外仍持有明文字节的 `bytes` / `bytearray` / `str` / `memoryview` 对象。
- 会话结束后仍存活的 `memoryview` 会触发 `ResourceWarning`，
  数量可查 `escaped_view_count()`。

## 设计要点

1. **唯一明文存储**：明文只存在于一个 `bytearray` 中；`bytes(secret)`、比较、
   哈希均被禁止，避免隐式产生不可清零的副本。
2. **可验证清零**：`destroy()` 对缓冲区做就地切片覆写（`buf[:] = b"\x00" * n`），
   销毁前签发的 `memoryview` 仍指向同一内存，可用于逐字节验证结果。
3. **幂等销毁**：销毁后内部缓冲区引用置 `None`，重复调用是空操作。
4. **副本检测**：`LeakScanner` 遍历 `gc.get_objects()` 与所有线程的完整调用栈
   （沿 `f_back`，显式物化 `f_locals`——CPython 3.12 中运行中的栈帧不被 GC
   枚举），按引用做有界 BFS，统计包含明文模式的对象；扫描前会先同步
   `f_locals` 并 `gc.collect()`，避免误报已删除的变量。

## 边界与限制（标准库范围内）

- `bytes` / `str` 一旦产生就无法可靠清零（不可变、可能被驻留），容器的策略是
  **阻止隐式复制 + 扫描暴露残留**，而不是事后擦除。
- 解释器/操作系统层面的残留（交换分区、core dump、已回收未覆写的内存页）
  超出纯标准库能力；`bytearray` 的就地覆写在 CPython 上会写回原内存，
  但语言规范不保证，跨解释器（PyPy 等）不承诺逐字节生效。
- 扫描是有界诊断（默认深度 3、对象上限 20 万），不是穷举证明。
