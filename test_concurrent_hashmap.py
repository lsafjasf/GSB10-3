"""concurrent_hashmap 自测（仅标准库 unittest）。

运行：
    python3 test_concurrent_hashmap.py -v

覆盖：
* 只读期间扩容 + 查询正确性断言
* 写入期间扩容（多写线程，不丢键、不出现两份）
* 迁移中逐步查询（新旧两区域并存时都被查询覆盖）
* 迁移中断（桶内中断点）与恢复结果
* 唯一迁移者 / 其余线程不被阻塞
* 边界：空表、单元素、空桶迁移、哈希全碰撞、迁移中覆盖与删除、缺键
* 迁移分批数据断言
"""

import threading
import time
import unittest

from concurrent_hashmap import ConcurrentHashMap, MigrationInterrupted


class CollidingKey:
    """强制哈希相同，制造全碰撞单桶用例。"""

    def __init__(self, ident):
        self.ident = ident

    def __hash__(self):
        return 0

    def __eq__(self, other):
        return isinstance(other, CollidingKey) and self.ident == other.ident

    def __repr__(self):
        return "K%d" % self.ident


class BasicTest(unittest.TestCase):
    def test_put_get_overwrite_delete(self):
        m = ConcurrentHashMap(capacity=4)
        self.assertIsNone(m.get("a"))
        self.assertEqual(m.get("a", 42), 42)
        self.assertFalse(m.delete("a"))
        m.put("a", 1)
        m.put("b", 2)
        self.assertEqual(len(m), 2)
        self.assertEqual(m.get("a"), 1)
        m.put("a", 10)  # 覆盖不产生两份
        self.assertEqual(m.get("a"), 10)
        self.assertEqual(len(m), 2)
        self.assertIn("a", m)
        self.assertTrue(m.delete("a"))
        self.assertNotIn("a", m)
        self.assertEqual(len(m), 1)
        m.check_invariants()

    def test_empty_table_resize(self):
        m = ConcurrentHashMap(capacity=4, batch_size=2)
        self.assertTrue(m.start_resize())
        while m.migrate_some() is True:
            pass
        self.assertFalse(m.migration_in_progress)
        self.assertEqual(len(m), 0)
        m.check_invariants()
        m.put("x", 1)
        self.assertEqual(m.get("x"), 1)

    def test_single_element_and_empty_buckets(self):
        m = ConcurrentHashMap(capacity=256, batch_size=4)
        m.put(7, "v")
        m.start_resize()  # 127 个空桶也要分批扫描
        rounds = 0
        while m.migrate_some() is True:
            rounds += 1
            m.check_invariants()  # 静默期每批后校验
            self.assertEqual(m.get(7), "v")
        self.assertEqual(m.get(7), "v")
        self.assertGreater(rounds, 1)  # 确认确实分了多批

    def test_all_keys_collide(self):
        m = ConcurrentHashMap(capacity=64, batch_size=2)
        keys = [CollidingKey(i) for i in range(20)]
        for i, k in enumerate(keys):
            m.put(k, i)
        self.assertEqual(len(m), 20)
        m.start_resize()
        m.migrate_all()
        self.assertFalse(m.migration_in_progress)
        for i, k in enumerate(keys):
            self.assertEqual(m.get(k), i)
        m.check_invariants()


class ResizeDuringReadOnlyTest(unittest.TestCase):
    def test_readers_only_while_migrating(self):
        m = ConcurrentHashMap(capacity=256, batch_size=4)
        model = {i: "v%d" % i for i in range(100)}
        for k, v in model.items():
            m.put(k, v)
        self.assertTrue(m.start_resize())

        errors = []
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                for k, v in model.items():
                    if m.get(k) != v:          # 查询正确性断言（迁移中）
                        errors.append("wrong value for %r" % k)
                        return
                if m.get("__missing__") is not None:
                    errors.append("missing key returned a value")
                    return

        readers = [threading.Thread(target=reader) for _ in range(3)]
        for r in readers:
            r.start()
        while m.migrate_some() is True:
            time.sleep(0.001)
        stop.set()
        for r in readers:
            r.join()
        self.assertEqual(errors, [])
        self.assertFalse(m.migration_in_progress)
        self.assertEqual(m.items(), model)
        m.check_invariants()


class ResizeDuringWritesTest(unittest.TestCase):
    def test_writers_continue_during_migration(self):
        m = ConcurrentHashMap(capacity=64, batch_size=3)
        base = {i: i * 7 for i in range(40)}
        for k, v in base.items():
            m.put(k, v)
        self.assertTrue(m.start_resize())

        n_threads, per = 4, 150
        barrier = threading.Barrier(n_threads + 2)
        done_writes = threading.Event()

        def migrator():
            barrier.wait()
            while not done_writes.is_set():
                r = m.migrate_some()
                if r is False:
                    break
                time.sleep(0.0005)  # 拉长迁移窗口，与写线程重叠

        def writer(tid):
            barrier.wait()
            for j in range(per):
                m.put(1000 + tid * per + j, tid * 10000 + j)

        threads = [threading.Thread(target=migrator)]
        threads += [threading.Thread(target=writer, args=(t,))
                    for t in range(n_threads)]
        for t in threads:
            t.start()
        barrier.wait()  # 主线程也参与，确保同时起跑
        for t in threads[1:]:
            t.join()
        done_writes.set()
        threads[0].join()
        m.migrate_all()

        model = dict(base)
        for t in range(n_threads):
            for j in range(per):
                model[1000 + t * per + j] = t * 10000 + j
        self.assertEqual(m.items(), model)   # 不丢键、不重复
        self.assertEqual(len(m), len(model))
        m.check_invariants()                  # 无两份 / 计数一致
        self.assertTrue(m.batch_log)          # 迁移确实分批发生


class QueryDuringMigrationTest(unittest.TestCase):
    def test_both_regions_covered_after_each_batch(self):
        m = ConcurrentHashMap(capacity=128, batch_size=2)
        model = {i: i * 10 for i in range(50)}
        for k, v in model.items():
            m.put(k, v)
        self.assertTrue(m.start_resize())

        saw_both_regions = False
        steps = []
        while True:
            old, cur = m._state
            old_keys = sum(len(b) for b in old.buckets)
            new_keys = sum(len(b) for b in cur.buckets)
            if old_keys and new_keys:
                saw_both_regions = True  # 新旧两区域同时持有数据

            # 迁移中途逐键断言：旧区和新区都必须被查询覆盖
            for k, v in model.items():
                self.assertEqual(m.get(k), v, "迁移中丢键 %r" % k)
            self.assertIsNone(m.get(10 ** 9))

            if old_keys:
                steps.append((old_keys, new_keys))
            r = m.migrate_some()
            if r is False:
                break
        # 打印迁移分批数据（每批后旧/新区键数）
        print("\n[迁移分批数据] (旧区键数, 新区键数):")
        for i, s in enumerate(steps):
            print("  批次 %2d: %s" % (i, s))

        m.migrate_all()
        self.assertFalse(m.migration_in_progress)
        self.assertTrue(saw_both_regions, "未出现新旧两区域并存阶段")
        self.assertEqual(m.items(), model)
        m.check_invariants()


class MigrationInterruptionTest(unittest.TestCase):
    def test_mid_bucket_interrupt_then_resume(self):
        # 20 个键哈希全部相同 -> 全在一个旧桶里，可精确制造桶内中断
        m = ConcurrentHashMap(capacity=64, batch_size=4)
        keys = [CollidingKey(i) for i in range(20)]
        for i, k in enumerate(keys):
            m.put(k, i)
        self.assertTrue(m.start_resize())

        # 中断点：旧桶内移动 4 个键之后（4 个在新区，16 个仍在旧桶）
        m.fail_after_moves = 4
        with self.assertRaises(MigrationInterrupted):
            m.migrate_some()
        m.fail_after_moves = None

        # ---- 中断后的一致状态 ----
        m.check_invariants()  # 每个键恰好一份，计数正确
        old, cur = m._state
        self.assertIs(old, old)
        self.assertTrue(m.migration_in_progress)
        for i, k in enumerate(keys):
            self.assertEqual(m.get(k), i)  # 新旧两区域合查，一个不丢

        # 迁移者锁必须已释放（否则下面调用会立刻拿到 None 而非继续迁移）
        self.assertIsNotNone(m.migrate_some())

        # ---- 恢复：从断点继续到完成 ----
        m.migrate_all()
        self.assertFalse(m.migration_in_progress)
        self.assertEqual(m.items(), {k: i for i, k in enumerate(keys)})
        m.check_invariants()

    def test_interrupt_after_single_move(self):
        m = ConcurrentHashMap(capacity=64, batch_size=8)
        for i in range(40):
            m.put(i, i)
        m.start_resize()
        m.fail_after_moves = 1
        with self.assertRaises(MigrationInterrupted):
            m.migrate_some()
        m.fail_after_moves = None
        # 旧桶标记可能未置位但桶已空：恢复路径必须能收敛
        m.migrate_all()
        for i in range(40):
            self.assertEqual(m.get(i), i)
        m.check_invariants()


class SingleMigratorTest(unittest.TestCase):
    def test_only_one_migrator_and_others_unblocked(self):
        m = ConcurrentHashMap(capacity=64, batch_size=1)
        for i in range(40):
            m.put(i, i)
        m.start_resize()

        # 占用迁移者锁，模拟“一个迁移者正在工作”
        self.assertTrue(m._migration_lock.acquire(blocking=False))
        try:
            result = []
            t = threading.Thread(target=lambda: result.append(m.migrate_some()))
            t.start()
            t.join(timeout=5)
            self.assertFalse(t.is_alive())        # 没有被阻塞
            self.assertEqual(result, [None])      # 试锁失败立即返回

            # 其余线程的读写完全不经过迁移者锁，照常进行
            m.put(1000, 1)
            self.assertEqual(m.get(1000), 1)
            self.assertEqual(m.get(0), 0)
            self.assertTrue(m.delete(1000))
        finally:
            m._migration_lock.release()

        m.migrate_all()
        m.check_invariants()


class OverwriteDeleteDuringMigrationTest(unittest.TestCase):
    def test_overwrite_and_delete(self):
        m = ConcurrentHashMap(capacity=128, batch_size=2)
        for i in range(50):
            m.put(i, 0)
        m.start_resize()
        m.migrate_some()  # 先迁一批，使新旧区都可能持有这些键
        for i in range(50):
            m.put(i, i + 1)       # 覆盖，不产生两份
        for i in range(0, 50, 2):
            self.assertTrue(m.delete(i))
        self.assertFalse(m.delete(9999))
        m.migrate_all()
        model = {i: i + 1 for i in range(50) if i % 2 == 1}
        self.assertEqual(m.items(), model)
        self.assertEqual(len(m), len(model))
        m.check_invariants()


class BatchLogTest(unittest.TestCase):
    def test_batch_size_bounded(self):
        m = ConcurrentHashMap(capacity=256, batch_size=5)
        for i in range(100):
            m.put(i, i)
        m.start_resize()
        m.migrate_all()
        self.assertTrue(m.batch_log)
        for moved, remaining in m.batch_log:
            self.assertGreaterEqual(moved, 0)
            self.assertLessEqual(moved, 5)  # 每批不超过 batch_size
        self.assertEqual(m.batch_log[-1][1], 0)
        total = sum(x[0] for x in m.batch_log)
        print("\n[迁移分批数据] 批次数=%d 迁移者搬迁桶总数=%d 每批=%s"
              % (len(m.batch_log), total, [x[0] for x in m.batch_log]))
        m.check_invariants()


if __name__ == "__main__":
    unittest.main(verbosity=2)
