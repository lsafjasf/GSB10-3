"""demo.py — 模拟一轮 fuzzing，展示语料库维护效果。

目标程序：微型 "魔数 + 长度 + 校验和 + 类型分支" 解析器。
输入来源：随机垃圾 / 结构化有效生成 / 种子变异（含长后缀垃圾以展示最小化）。
用法：python3 demo.py [迭代次数] [容量]
"""

import random
import sys

from corpus import Corpus

KINDS = (0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0xFF)


def target(data):
    """b'FZ' + 长度 + 载荷(首字节为类型) + 校验和。"""
    if len(data) < 3:
        return False
    if data[0] != 0x46:                      # 'F'
        return False
    if data[1] != 0x5A:                      # 'Z'
        return False
    n = data[2]
    if n == 0:
        return True
    if len(data) < 3 + n + 1:
        return False
    payload = data[3:3 + n]
    if data[3 + n] != (sum(payload) & 0xFF):
        return False
    kind = payload[0]
    if kind == 0x01:
        depth = 1
        if n >= 2 and payload[1] == 0x07:    # 深层分支（覆盖是浅层超集）
            depth = 2
        return depth > 0
    elif kind == 0x02:
        return True
    elif kind == 0x03:
        return True
    elif kind == 0x04:
        return True
    elif kind == 0x05:
        return True
    elif kind == 0x06:
        return True
    elif kind == 0xFF:
        return True
    return True


def gen_valid(rng):
    """生成校验和正确的有效输入，类型有偏向。"""
    n = rng.randint(1, 5)
    payload = bytearray(rng.randrange(256) for _ in range(n))
    if rng.random() < 0.6:
        payload[0] = rng.choice(KINDS)
        if payload[0] == 0x01 and rng.random() < 0.5 and n >= 2:
            payload[1] = 0x07                # 触发深层分支
    chk = sum(payload) & 0xFF
    return b"FZ" + bytes([n]) + bytes(payload) + bytes([chk])


def mutate(rng, seed):
    data = bytearray(seed)
    for _ in range(rng.randint(1, 4)):
        op = rng.random()
        if op < 0.5 and data:
            data[rng.randrange(len(data))] = rng.randrange(256)
        elif op < 0.8:
            data.insert(rng.randrange(len(data) + 1), rng.randrange(256))
        elif data:
            del data[rng.randrange(len(data))]
    return bytes(data)


def main():
    iterations = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    capacity = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    rng = random.Random(20261004)

    corpus = Corpus(target, capacity=capacity)
    corpus.add(b"FZ\x00")
    corpus.add(b"FZ\x01\x01\x01")          # 初始种子：kind=0x01 浅层分支

    phase_admitted = []
    phase_size = 1000
    admitted_at_phase_start = 0

    for i in range(iterations):
        roll = rng.random()
        if roll < 0.25:                     # 随机垃圾（多半无效）
            candidate = bytes(rng.randrange(256) for _ in range(rng.randint(0, 12)))
        elif roll < 0.55:                   # 结构化有效输入
            candidate = gen_valid(rng)
        else:                               # 种子变异
            candidate = mutate(rng, rng.choice(corpus.entries)[0])
        if rng.random() < 0.15:             # 长后缀垃圾：展示最小化
            candidate += b"\x00" * rng.randint(10, 120)
        corpus.add(candidate)

        if (i + 1) % phase_size == 0:
            phase_admitted.append(corpus.n_admitted - admitted_at_phase_start)
            admitted_at_phase_start = corpus.n_admitted

    print(corpus.report())
    print()
    print("分阶段入库数（每 %d 个输入，展示收益递减）:" % phase_size)
    print("  " + ", ".join(str(x) for x in phase_admitted))
    print()
    print("最终语料库条目:")
    for data, cov in corpus.entries:
        print("  %-28s %3d 字节  覆盖 %2d 行" % (data.hex(), len(data), len(cov)))


if __name__ == "__main__":
    main()
