"""压缩率对比基准：不同训练样本量下，字典对短消息压缩的收益。

运行：python3 benchmark.py
"""

import json
import random

import dictz

TEST_COUNT = 300          # 固定的测试消息数
DICT_SIZE = 1024          # 字典大小（字节），只存一次，不随消息传输
TRAIN_SIZES = [0, 5, 20, 100, 500]


def make_messages(seed, n):
    rng = random.Random(seed)
    events = ["login", "logout", "purchase", "click", "signup", "page_view"]
    texts = [
        "user action completed",
        "request processed successfully",
        "cache miss, fallback to db",
        "session expired, redirect to login",
    ]
    msgs = []
    for _ in range(n):
        msg = {
            "ts": 1700000000 + rng.randint(0, 99999),
            "level": rng.choice(["INFO", "DEBUG", "WARN", "ERROR"]),
            "event": rng.choice(events),
            "user_id": rng.randint(1000, 9999),
            "session": "%08x" % rng.getrandbits(32),
            "message": rng.choice(texts),
        }
        msgs.append(json.dumps(msg, separators=(",", ":")).encode())
    return msgs


def main():
    test_set = make_messages(seed=999, n=TEST_COUNT)
    raw_total = sum(len(m) for m in test_set)
    plain_total = sum(len(dictz.compress(m)) for m in test_set)

    print("测试集: %d 条短消息, 原始共 %d 字节 (平均 %.1f 字节/条)"
          % (TEST_COUNT, raw_total, raw_total / TEST_COUNT))
    print("字典大小: %d 字节 (持久化一次, 不随消息传输)" % DICT_SIZE)
    print()
    header = ("训练样本量", "压缩总字节", "平均字节/条", "压缩率(vs原始)", "相对无字典")
    print("%-10s %-10s %-12s %-14s %s" % header)
    print("-" * 64)
    print("%-10s %-10d %-12.1f %-14s %s" % (
        "无字典", plain_total, plain_total / TEST_COUNT,
        "%.1f%%" % (100.0 * plain_total / raw_total), "-"))

    for size in TRAIN_SIZES:
        train_set = make_messages(seed=1000 + size, n=size)
        dictionary = dictz.train_dictionary(train_set, dict_size=DICT_SIZE)
        total = sum(len(dictz.compress(m, dictionary)) for m in test_set)
        saving = 100.0 * (1 - total / plain_total)
        print("%-10s %-10d %-12.1f %-14s %s" % (
            size if size else "0(空字典)", total, total / TEST_COUNT,
            "%.1f%%" % (100.0 * total / raw_total),
            "省 %.1f%%" % saving if saving >= 0 else "差 %.1f%%" % -saving))

    print()
    print("注: 压缩率 = 压缩后 / 原始; 相对无字典列为带字典相对无字典的体积变化。")


if __name__ == "__main__":
    main()

