"""固定评估样本集。

所有样本由确定性规则生成，不读取任何随机源（不使用 random/os.urandom），
与“待测 RNG 实现”完全无关，保证每次运行结果可复现。

样本清单（默认 SAMPLE_BYTES = 16384 字节）：
  good_sha256_counter  正常样本：SHA-256 计数器模式展开的确定性字节流，
                       密码学散列输出在统计上与随机不可区分，应全部 PASS。
  low_nibble_fixed     低位固定：每字节低 4 位恒为 0xA（高 4 位取自正常流），
                       应被位平衡（逐位）、游程、周期性查出。
  period_2bytes        短周期：0x00 0xFF 交替，周期 2 字节。
  period_16bytes       短周期：16 字节平衡模式（8 对互补字节，位平衡恰好
                       通过，用于展示周期性检查独立于位平衡的价值）。
  all_zeros            全零输出：所有字节为 0x00，四类检查应全部 FAIL。
"""

import hashlib

SAMPLE_BYTES = 16384

# 16 字节平衡模式：8 对互补字节，共 64 个 1 位，整体位平衡精确 50%。
_PERIOD_16 = bytes((
    0x3C, 0xC3, 0x5A, 0xA5, 0x66, 0x99, 0x0F, 0xF0,
    0x33, 0xCC, 0x55, 0xAA, 0x17, 0xE8, 0x81, 0x7E,
))


def _sha256_counter_stream(n):
    """SHA-256(8 字节大端计数器) 拼接展开，确定性、无随机源。"""
    out = bytearray()
    counter = 0
    while len(out) < n:
        out += hashlib.sha256(counter.to_bytes(8, "big")).digest()
        counter += 1
    return bytes(out[:n])


def _low_nibble_fixed(n):
    stream = _sha256_counter_stream(n)
    return bytes((b & 0xF0) | 0x0A for b in stream)


def _repeated(pattern, n):
    reps, rem = divmod(n, len(pattern))
    return pattern * reps + pattern[:rem]


def make_samples(n=SAMPLE_BYTES):
    """返回 {样本名: bytes}，内容完全确定。"""
    return {
        "good_sha256_counter": _sha256_counter_stream(n),
        "low_nibble_fixed": _low_nibble_fixed(n),
        "period_2bytes": _repeated(b"\x00\xff", n),
        "period_16bytes": _repeated(_PERIOD_16, n),
        "all_zeros": bytes(n),
    }
