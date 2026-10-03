"""演示：输出膨胀度量数据、重建切换对拍与切换前后耗时/空间对比。

运行：python3 demo.py
"""

import threading
import time

from index_bloat import (
    ENTRY_SIZE,
    IndexStore,
    OnlineRebuilder,
    RebuildAborted,
)

MIN_ENTRIES = 1000
MAX_LIVE_RATIO = 0.5


def build_workload(initial, updates, deletes):
    """产生指定规模的增删改负载。"""
    store = IndexStore()
    for i in range(initial):
        store.put("key-%08d" % i, "v%d" % i)
    for i in range(updates):
        key = "key-%08d" % (i % initial)
        store.put(key, "v%d-u%d" % (i % initial, i))
    for i in range(deletes):
        store.delete("key-%08d" % i)
    return store


def print_metrics(title, index):
    rep = index.measure(MIN_ENTRIES, MAX_LIVE_RATIO)
    print("  %-22s live=%6d total=%6d 死条目=%6d ratio=%.3f 空间=%8dB  重建? %s"
          % (title, rep.live_entries, rep.total_entries, rep.dead_entries,
             rep.live_ratio, rep.space_bytes,
             "是" if rep.should_rebuild else "否"))
    return rep


def random_probes(store, n):
    """对拍：对活跃索引做 n 次点查，返回结果快照。"""
    snap = {}
    for i in range(n):
        key = "key-%08d" % (i * 7919)
        snap[key] = store.get(key)
    return snap


def main():
    print("=" * 78)
    print("场景 1：膨胀度量（有效条目 / 占用空间）")
    print("判据：total >= %d 且 live_ratio < %.2f 才重建" % (MIN_ENTRIES, MAX_LIVE_RATIO))
    print("=" * 78)

    healthy = build_workload(initial=1000, updates=200, deletes=50)
    print_metrics("轻微膨胀", healthy.active())

    severe = build_workload(initial=10000, updates=40000, deletes=2000)
    rep = print_metrics("严重膨胀", severe.active())

    tiny = IndexStore()
    for i in range(50):
        tiny.put("k%d" % i, 1)
        tiny.put("k%d" % i, 2)
    print_metrics("小规模不触发", tiny.active())
    print("  判据说明：%s" % rep.reason)

    print()
    print("=" * 78)
    print("场景 2：严重膨胀 -> 在线重建，切换前/后查询对拍与耗时对比")
    print("=" * 78)

    before_probes = random_probes(severe, 500)
    before_scan = severe.scan("key-0000")
    report = OnlineRebuilder(severe).rebuild()
    after_probes = random_probes(severe, 500)
    after_scan = severe.scan("key-0000")

    mismatch_points = sum(1 for k, v in before_probes.items() if after_probes[k] != v)
    print("  点查对拍：500 个 key，切换前后不一致 %d 处" % mismatch_points)
    print("  扫描对拍：前缀 key-0000 结果集一致 -> %s" % (before_scan == after_scan))
    print("  影子对拍：重建提交时全量比对 %d 个 key 位，不一致 %d 处"
          % (report.checked_keys, report.mismatches))
    print("  空间占用：%.1f KB -> %.1f KB（回收 %.1f%%）"
          % (report.space_before_bytes / 1024,
             report.space_after_bytes / 1024,
             100 * (1 - report.space_after_bytes / report.space_before_bytes)))
    print("  scan耗时：%.3f ms -> %.3f ms（%.2fx）"
          % (report.scan_ms_before, report.scan_ms_after,
             report.scan_ms_before / report.scan_ms_after))

    print()
    print("=" * 78)
    print("场景 3：重建期间持续写入（写线程与重建并发，最终数据不丢）")
    print("=" * 78)

    loaded = build_workload(initial=10000, updates=40000, deletes=2000)
    rebuilder = OnlineRebuilder(loaded)
    snapshot = loaded.begin_rebuild()
    # 手工分阶段，确保写入确实发生在"构建中"
    new_index = type(loaded.active())()
    for i, (key, value) in enumerate(snapshot.items()):
        new_index.put(key, value)
        if i == 5000:
            # 构建进行到一半，并发追加一批写入
            def writer():
                for j in range(2000):
                    loaded.put("hot-%06d" % j, "hot%d" % j)
                loaded.delete("key-00000000")
            t = threading.Thread(target=writer)
            t.start()
            t.join()
    old, check = loaded.commit_rebuild(new_index)
    print("  构建中并发写入 2000 个 hot-* key、删除 1 个 key")
    print("  影子对拍不一致 %d 处" % check["mismatches"])
    print("  抽查 hot-001234=%r  hot-000000=%r  key-00000000=%r"
          % (loaded.get("hot-001234"), loaded.get("hot-000000"),
             loaded.get("key-00000000")))
    assert loaded.get("hot-001234") == "hot1234"
    assert loaded.get("hot-000000") == "hot0"
    assert loaded.get("key-00000000") is None
    print("  -> 并发写入全部追平，delta 回放正确")

    print()
    print("=" * 78)
    print("场景 4：重建被中断（注入故障）-> 旧索引可用，重试成功")
    print("=" * 78)

    again = build_workload(initial=10000, updates=40000, deletes=2000)
    probes_before = random_probes(again, 300)
    total_before = again.active().total_entries
    try:
        OnlineRebuilder(again).rebuild(fail_after_puts=5000)
    except RebuildAborted as exc:
        print("  第 1 次重建中断：%s" % exc)
    print("  中断后旧索引仍在服务：total=%d（未变 %s），300 点查一致 %s"
          % (again.active().total_entries,
             again.active().total_entries == total_before,
             random_probes(again, 300) == probes_before))
    report2 = OnlineRebuilder(again).rebuild()
    print("  重试重建：影子对拍不一致 %d 处，空间 %.1f KB -> %.1f KB"
          % (report2.mismatches,
             report2.space_before_bytes / 1024,
             report2.space_after_bytes / 1024))
    print("  重试点查对拍一致 -> %s" % (random_probes(again, 300) == probes_before))


if __name__ == "__main__":
    main()
