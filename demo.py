"""演示：多键并行分发 -> 模拟崩溃 -> 重启重投 -> 全局重排 + 位点样例。

运行：python3 demo.py [state_dir]   （默认 ./demo_state，每次运行前清空）
"""

import os
import shutil
import sys
import threading
import time

from changeflow import (
    ChangeLog,
    Dispatcher,
    OffsetStore,
    assert_per_key_order,
    global_reorder,
)

state_dir = sys.argv[1] if len(sys.argv) > 1 else "demo_state"
shutil.rmtree(state_dir, ignore_errors=True)
os.makedirs(state_dir)

log = ChangeLog(os.path.join(state_dir, "changes.log"))
offsets = OffsetStore(state_dir)

# ---- 1. 产生变更：3 个主键交错写入 ---------------------------------------
keys = ["user:1", "user:2", "user:3"]
for round_no in range(3):
    for key in keys:
        log.append(key, "update", {"round": round_no})
print(f"[产生] 9 条变更，位点 1..9，主键交错：{keys} x 3 轮\n")

# ---- 2. 第一次消费：前 3 条确认后"崩溃" ----------------------------------
release = threading.Event()
processed = []
plock = threading.Lock()


def flaky_consumer(change, ack):
    if change.seq <= 3:
        time.sleep(0.05)
        with plock:
            processed.append(change)
        ack(change.seq)
    else:
        release.wait(3.0)  # 在途但未确认 -> 进程崩溃，位点丢失


d1 = Dispatcher(log, offsets, flaky_consumer, workers=4,
                auto_ack=False, delivery_timeout=5.0)
d1.start()
deadline = time.monotonic() + 5
while time.monotonic() < deadline and len(d1.delivery_map().get("user:3", ())) < 2:
    time.sleep(0.01)
d1.crash_stop()
release.set()
print(f"[崩溃] 已确认位点 1..3，水位线 watermark={offsets.watermark}")
print(f"[崩溃] 位点 4..9 在途未确认，位点记录已持久化：\n")

# ---- 3. 位点记录样例 ------------------------------------------------------
with open(os.path.join(state_dir, "watermark.json")) as fh:
    print(f"  watermark.json -> {fh.read().strip()}")
with open(os.path.join(state_dir, "acks.log")) as fh:
    print(f"  acks.log       -> {fh.read().strip() or '(空：<=水位线的确认已压缩进水位线)'}")
print()

# ---- 4. 重启：恢复位点，重投未确认变更 ------------------------------------
offsets2 = OffsetStore(state_dir)
print(f"[重启] 从磁盘恢复位点，watermark={offsets2.watermark}，未确认变更将重新投递\n")

recovered = []
rlock = threading.Lock()


def parallel_consumer(change, ack):
    time.sleep(0.1)  # 放大并行窗口
    with rlock:
        recovered.append(change)
    ack(change.seq)


d2 = Dispatcher(log, offsets2, parallel_consumer, workers=4, delivery_timeout=2.0)
t0 = time.monotonic()
d2.start()
assert d2.join_drained(10)
elapsed = time.monotonic() - t0
d2.stop()

print(f"[重启后投递] 实际投递位点（含并行完成乱序）: "
      f"{[c.seq for c in recovered]}")
print(f"[并行] 6 条变更各耗时 0.1s，总耗时 {elapsed:.2f}s（串行需 0.6s）\n")

# ---- 5. 顺序断言 + 全局重排样例 -------------------------------------------
expected = {"user:1": [4, 7], "user:2": [5, 8], "user:3": [6, 9]}
assert_per_key_order(d2.delivery_map(), expected)
print(f"[顺序断言] 每个主键去重后投递顺序 == 产生顺序，乱序检测 = 0 ✓")
for key in keys:
    print(f"  {key}: 投递 {d2.delivered_for(key)}  期望 {expected[key]}")

reordered = global_reorder(recovered)
print(f"\n[全局重排] 并行结果按位点重排 -> {[c.seq for c in reordered]}")
print(f"[位点] 最终水位线 watermark={offsets2.watermark}（全部确认）")
