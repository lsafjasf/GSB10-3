# -*- coding: utf-8 -*-
"""
演示：断点续传 + 位点被清理时的缺口报告。

运行：python3 demo_gap.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from replication import (
    CheckpointStore,
    LocalTransport,
    LogSource,
    Puller,
    SequenceSink,
)


def main():
    tmpdir = tempfile.mkdtemp(prefix="repl-demo-")
    ckpt = os.path.join(tmpdir, "checkpoint.json")

    source = LogSource()
    source.append_many(["rec-%d" % i for i in range(20)])
    sink = SequenceSink()

    print("== 阶段 1：正常拉取 2 批（batch_size=5）后模拟重启 ==")
    puller = Puller(LocalTransport(source), CheckpointStore(ckpt), sink,
                    batch_size=5)
    stats = puller.pull_once(max_batches=2)
    print("applied:", sink.applied_lsns)
    print("checkpoint on disk:", CheckpointStore(ckpt).load())

    print("\n== 阶段 2：重启后续传（新 Puller，位点来自 checkpoint）==")
    puller = Puller(LocalTransport(source), CheckpointStore(ckpt), sink,
                    batch_size=5)
    stats = puller.pull_once(max_batches=1)
    print("applied:", sink.applied_lsns)
    print("checkpoint on disk:", CheckpointStore(ckpt).load())

    print("\n== 阶段 3：源端清理日志，位点 15 已被清理 ==")
    source.purge_below(18)  # lsn < 18 全部被清理
    puller = Puller(LocalTransport(source), CheckpointStore(ckpt), sink,
                    batch_size=5)
    stats = puller.pull_once()
    if stats.gap is not None:
        print("GAP REPORT:")
        print(json.dumps(stats.gap.to_dict(), indent=2, ensure_ascii=False))
    print("applied 序列未被推进:", sink.applied_lsns)
    print("checkpoint 仍为:", CheckpointStore(ckpt).load())


if __name__ == "__main__":
    main()
