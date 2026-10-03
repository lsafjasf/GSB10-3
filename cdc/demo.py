"""端到端演示：多键并行消费 -> 全局重排 -> 崩溃重启重放。

运行：python3 demo.py
"""

import os
import random
import tempfile

from cdc import ChangeLog, CheckpointStore, Dispatcher, ParallelConsumer, ReorderBuffer, _SimulatedCrash


def main():
    random.seed(7)
    tmp = tempfile.mkdtemp(prefix="cdc-demo-")
    log = ChangeLog(os.path.join(tmp, "changes.jsonl"))
    cps = CheckpointStore(os.path.join(tmp, "checkpoints.json"))
    dispatcher = Dispatcher(3)

    pks = ["alice", "bob", "carol", "dave"]
    for i in range(12):
        log.append(pks[i % len(pks)], "UPDATE", {"i": i})

    print("== 1. 变更日志（产生顺序，lsn 即全局位点）==")
    for c in log.all():
        print(f"  lsn={c.lsn:2d}  pk={c.pk:6s} op={c.op}")

    delivered = []

    def handler(change):
        delivered.append(change)
        import time
        time.sleep(random.random() * 0.005)

    consumer = ParallelConsumer("group-A", log, cps, dispatcher)
    consumer.process_batch(handler)

    print("\n== 2. 并行消费完成顺序（不同主键交错，允许乱序）==")
    print("  ", [c.lsn for c in delivered])

    per_key = {}
    for c in delivered:
        per_key.setdefault(c.pk, []).append(c.lsn)
    print("\n== 3. 每个主键的投递序列（必须严格递增）==")
    for pk, seq in sorted(per_key.items()):
        ok = all(a < b for a, b in zip(seq, seq[1:]))
        print(f"  {pk:6s} {seq}  有序={ok}")
    assert all(all(a < b for a, b in zip(s, s[1:])) for s in per_key.values())

    buf = ReorderBuffer(expected_lsn=1)
    reordered = []
    for c in delivered:
        buf.add(c)
        reordered.extend(buf.drain())
    print("\n== 4. ReorderBuffer 重排后的全局顺序（重排样例）==")
    print("  ", [(c.lsn, c.pk) for c in reordered])
    assert [c.lsn for c in reordered] == list(range(1, 13))

    print("\n== 5. 位点记录样例（checkpoints.json 落盘内容）==")
    for k, v in sorted(cps.snapshot().items()):
        print(f"  {k} -> lsn {v}")

    # 追加新变更并模拟崩溃
    for i in range(12, 20):
        log.append(pks[i % len(pks)], "UPDATE", {"i": i})
    crash_lsn = 15
    try:
        consumer.process_batch(handler, crash_after=lambda c: c.lsn == crash_lsn)
    except _SimulatedCrash:
        print(f"\n== 6. 模拟崩溃：lsn={crash_lsn} 已处理但位点未提交 ==")
    victim = dispatcher.partition_for(next(c.pk for c in log.all() if c.lsn == crash_lsn))
    print(f"  崩溃分区 p{victim} 位点停在 {cps.get(f'group-A:p{victim}')}")

    print("\n== 7. 重启（重新构造消费者），未确认变更重新投递 ==")
    consumer2 = ParallelConsumer("group-A", log, cps, dispatcher)
    redelivered = consumer2.process_batch(handler)
    print("  重放的 lsn:", sorted(c.lsn for c in redelivered))
    print("  重启后位点:", {k: v for k, v in sorted(cps.snapshot().items())})
    print(f"\n演示数据目录: {tmp}")


if __name__ == "__main__":
    main()
