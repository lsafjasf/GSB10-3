"""index_bloat 自测：python3 -m unittest test_index_bloat -v"""

import threading
import unittest

from index_bloat import (
    ENTRY_SIZE,
    IndexStore,
    LogIndex,
    OnlineRebuilder,
    RebuildAborted,
)


def make_store(initial=1000, updates=0, deletes=0):
    store = IndexStore()
    for i in range(initial):
        store.put("key-%06d" % i, "v%d" % i)
    for i in range(updates):
        store.put("key-%06d" % (i % initial), "u%d" % i)
    for i in range(deletes):
        store.delete("key-%06d" % i)
    return store


class TestBloatMetrics(unittest.TestCase):
    def test_slight_bloat_not_triggered(self):
        # 轻微膨胀：ratio=0.76，不触发重建
        store = make_store(initial=1000, updates=200, deletes=50)
        rep = store.active().measure(min_entries=1000, max_live_ratio=0.5)
        self.assertEqual(rep.live_entries, 950)
        self.assertEqual(rep.total_entries, 1250)
        self.assertAlmostEqual(rep.live_ratio, 950 / 1250)
        self.assertFalse(rep.should_rebuild)

    def test_severe_bloat_triggered(self):
        # 严重膨胀：大量覆盖写，ratio≈0.15，触发重建
        store = make_store(initial=10000, updates=40000, deletes=2000)
        rep = store.active().measure(min_entries=1000, max_live_ratio=0.5)
        self.assertEqual(rep.live_entries, 8000)
        self.assertEqual(rep.total_entries, 52000)
        self.assertLess(rep.live_ratio, 0.5)
        self.assertTrue(rep.should_rebuild)
        self.assertEqual(rep.space_bytes, 52000 * ENTRY_SIZE)

    def test_small_index_not_triggered(self):
        # 边界：日志规模低于 min_entries，即使 ratio 低也不重建
        store = IndexStore()
        for i in range(50):
            store.put("k%d" % i, 1)
            store.put("k%d" % i, 2)
        rep = store.active().measure(min_entries=1000, max_live_ratio=0.5)
        self.assertFalse(rep.should_rebuild)
        self.assertIn("太小", rep.reason)

    def test_empty_index_ratio_is_one(self):
        # 边界：空索引 ratio 定义为 1.0，不触发
        rep = LogIndex().measure()
        self.assertEqual(rep.live_ratio, 1.0)
        self.assertFalse(rep.should_rebuild)

    def test_delete_nonexistent_key_still_bloats(self):
        # 边界：删除不存在的 key 也写墓碑（与真实日志索引一致），计入膨胀
        idx = LogIndex()
        idx.put("a", 1)
        idx.delete("ghost")
        self.assertEqual(idx.total_entries, 2)
        self.assertEqual(idx.live_entries, 1)
        self.assertIsNone(idx.get("ghost"))


class TestOnlineRebuild(unittest.TestCase):
    def test_rebuild_compacts_and_preserves_data(self):
        store = make_store(initial=10000, updates=40000, deletes=2000)
        before = store.active().items()
        report = OnlineRebuilder(store).rebuild()
        self.assertEqual(report.status, "switched")
        self.assertEqual(report.mismatches, 0)
        # 数据完全一致
        self.assertEqual(store.active().items(), before)
        # 空间显著回收：新索引每个有效 key 只有一条日志
        self.assertEqual(store.active().total_entries, 8000)
        self.assertLess(report.space_after_bytes, report.space_before_bytes)
        # 切换后扫描更快
        self.assertLess(report.scan_ms_after, report.scan_ms_before)

    def test_switch_is_atomic_and_results_identical(self):
        # 切换前后对拍：同一批查询结果必须完全一致
        store = make_store(initial=5000, updates=20000, deletes=1000)
        probes = ["key-%06d" % (i * 13 % 7000) for i in range(400)]
        probes += ["missing-%d" % i for i in range(100)]
        before = {k: store.get(k) for k in probes}
        scan_before = store.scan("key-000")
        OnlineRebuilder(store).rebuild()
        after = {k: store.get(k) for k in probes}
        self.assertEqual(before, after)
        self.assertEqual(scan_before, store.scan("key-000"))

    def test_concurrent_readers_see_consistent_index_during_switch(self):
        # 原子性：读线程在切换窗口内永不抛异常、永不读到撕裂状态
        store = make_store(initial=5000, updates=20000, deletes=1000)
        expected = store.active().items()
        errors = []
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    items = store.active().items()
                    if items != expected:
                        errors.append("内容不一致")
                except Exception as exc:  # noqa: BLE001
                    errors.append(repr(exc))

        threads = [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        OnlineRebuilder(store).rebuild()
        stop.set()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])

    def test_writes_during_rebuild_are_captured(self):
        # 重建中持续写入：delta 日志保证不丢
        store = make_store(initial=10000, updates=40000, deletes=2000)
        rebuilder = OnlineRebuilder(store)

        def writer():
            for j in range(3000):
                store.put("hot-%05d" % j, "hot%d" % j)
            store.delete("key-000000")

        t = threading.Thread(target=writer)
        t.start()
        report = rebuilder.rebuild()
        t.join()
        # 写线程可能与重建交错，补一次重建确保追平（幂等）
        if store.get("hot-02999") != "hot2999":
            report = OnlineRebuilder(store).rebuild()
        self.assertEqual(report.mismatches, 0)
        self.assertEqual(store.get("hot-02999"), "hot2999")
        self.assertEqual(store.get("hot-00000"), "hot0")
        self.assertIsNone(store.get("key-000000"))

    def test_interrupted_rebuild_keeps_old_index_and_retry_works(self):
        # 重建被中断：旧索引继续服务，数据不变，可重试
        store = make_store(initial=10000, updates=40000, deletes=2000)
        before = store.active().items()
        total_before = store.active().total_entries
        with self.assertRaises(RebuildAborted):
            OnlineRebuilder(store).rebuild(fail_after_puts=5000)
        # 旧索引原样服务
        self.assertEqual(store.active().items(), before)
        self.assertEqual(store.active().total_entries, total_before)
        # 中断后写入仍正常
        store.put("after-abort", "ok")
        self.assertEqual(store.get("after-abort"), "ok")
        # 重试成功
        report = OnlineRebuilder(store).rebuild()
        self.assertEqual(report.mismatches, 0)
        self.assertEqual(store.get("after-abort"), "ok")
        self.assertEqual(store.active().total_entries, 8001)

    def test_interrupted_rebuild_with_concurrent_writes_loses_nothing(self):
        # 中断 + 并发写：abort 后 delta 丢弃但写已落到旧索引
        store = make_store(initial=5000, updates=10000)
        snapshot = store.begin_rebuild()
        store.put("during", "x")
        store.abort_rebuild()
        self.assertEqual(store.get("during"), "x")
        report = OnlineRebuilder(store).rebuild()
        self.assertEqual(store.get("during"), "x")
        self.assertEqual(report.mismatches, 0)

    def test_double_begin_rebuild_replaces_delta(self):
        # 边界：重复 begin 不泄漏，旧 delta 被覆盖，最终一致
        store = make_store(initial=2000, updates=5000)
        store.begin_rebuild()
        store.put("a", 1)
        store.begin_rebuild()  # 重新开始
        store.put("b", 2)
        store.abort_rebuild()
        report = OnlineRebuilder(store).rebuild()
        self.assertEqual(report.mismatches, 0)
        self.assertEqual(store.get("a"), 1)
        self.assertEqual(store.get("b"), 2)


if __name__ == "__main__":
    unittest.main()
