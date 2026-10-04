#!/usr/bin/env python3
"""不同训练样本量下的压缩率对比。

生成一组字段固定、取值随机的短 JSON 日志（单条几十到一百多字节），
分别在"无字典 / 10 条样本训练 / 100 条 / 1000 条"下逐条压缩，
统计平均体积与压缩率。
"""

from __future__ import annotations

import json
import random

from lzdict import compress, train_dictionary, dict_id_for

LEVELS = ["INFO", "WARN", "ERROR"]
SERVICES = ["auth", "billing", "gateway", "search"]
EVENTS = [
    "login_success", "login_failed", "payment_created", "payment_failed",
    "rate_limit", "query_timeout", "request_done", "cache_miss",
]
MESSAGES = [
    "user authenticated", "card declined by issuer", "upstream responded slow",
    "index rebuilt", "token refreshed", "queue drained",
]


def make_message(rng: random.Random) -> bytes:
    record = {
        "ts": "2026-10-%02dT%02d:%02d:%02dZ" % (
            rng.randint(1, 28), rng.randint(0, 23), rng.randint(0, 59),
            rng.randint(0, 59)),
        "level": rng.choice(LEVELS),
        "service": rng.choice(SERVICES),
        "event": rng.choice(EVENTS),
        "user_id": rng.randint(100000, 999999),
        "latency_ms": rng.randint(1, 900),
        "message": rng.choice(MESSAGES),
    }
    return json.dumps(record, separators=(",", ":"), sort_keys=True).encode()


def main() -> None:
    rng = random.Random(20261004)
    eval_messages = [make_message(rng) for _ in range(500)]
    raw_total = sum(len(m) for m in eval_messages)

    rows = []
    for n_train in (0, 10, 100, 1000):
        if n_train:
            rng_train = random.Random(42 + n_train)
            train_samples = [make_message(rng_train) for _ in range(n_train)]
            dictionary = train_dictionary(train_samples)
            did = dict_id_for(dictionary)
        else:
            dictionary, did = b"", ""
        total = 0
        for msg in eval_messages:
            total += len(compress(msg, dictionary, did))
        rows.append((n_train, len(dictionary), raw_total, total))

    print("评测集: 500 条短 JSON 日志, 原始总体积 %d 字节" % raw_total)
    print()
    print("| 训练样本量 | 字典大小 | 压缩后总字节 | 平均/条 | 压缩率 | 相对无字典 |")
    print("|-----------:|---------:|-------------:|--------:|-------:|-----------:|")
    base_total = rows[0][3]
    for n_train, dict_size, raw, total in rows:
        ratio = total / raw
        vs_base = total / base_total
        label = str(n_train) if n_train else "0（不用字典）"
        print(f"| {label} | {dict_size} | {total} | {total / 500:.1f} "
              f"| {ratio:.3f} | {vs_base:.3f} |")

    print()
    # 单条典型消息的直观对比
    sample = eval_messages[0]
    print("典型单条消息 (%d 字节):" % len(sample))
    print("  原文: %s" % sample.decode())
    print("  无字典压缩后: %d 字节" % len(compress(sample)))
    dictionary = train_dictionary(
        [make_message(random.Random(i)) for i in range(1000)])
    did = dict_id_for(dictionary)
    print("  字典压缩后:   %d 字节 (dict_id=%s)"
          % (len(compress(sample, dictionary, did)), did))


if __name__ == "__main__":
    main()
