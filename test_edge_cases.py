"""边界用例测试。运行: python3 test_edge_cases.py -v"""

import threading
import unittest

from refcount import (
    ObjectRetiredError,
    ReaderSectionError,
    ReclaimManager,
    UseAfterReleaseError,
)


class TestEdgeCases(unittest.TestCase):
    def test_empty_manager_reclaim_is_noop(self):
        mgr = ReclaimManager()
        self.assertEqual(mgr.reclaim(), 0)
        self.assertEqual(mgr.pending_count, 0)

    def test_max_refcount_one(self):
        mgr = ReclaimManager(max_refcount=1)
        h = mgr.create("only-one")
        from refcount import RefCountOverflowError
        with self.assertRaises(RefCountOverflowError):
            h._obj.acquire()
        h.release()
        self.assertEqual(mgr.reclaim(), 1)

    def test_retire_while_reader_active_then_reclaim_after_exit(self):
        destroyed = []
        mgr = ReclaimManager()
        h = mgr.create("v", on_destroy=destroyed.append)
        with mgr.reader():
            h.release()
            self.assertEqual(mgr.reclaim(), 0)   # 临界区内不回收
            self.assertEqual(destroyed, [])
        self.assertEqual(mgr.reclaim(), 1)       # 退出后即可回收
        self.assertEqual(destroyed, ["v"])

    def test_nested_reader_sections(self):
        mgr = ReclaimManager()
        h = mgr.create("v")
        obj = h._obj
        with mgr.reader():
            with mgr.reader():           # 可重入
                h.release()
                self.assertEqual(mgr.reclaim(), 0)
            self.assertEqual(mgr.reclaim(), 0)   # 外层临界区仍在保护
            self.assertEqual(obj.peek(), "v")
        self.assertEqual(mgr.reclaim(), 1)

    def test_peek_outside_reader_section_rejected(self):
        mgr = ReclaimManager()
        h = mgr.create("v")
        with self.assertRaises(ReaderSectionError):
            h._obj.peek()
        h.release()
        mgr.reclaim()

    def test_reader_entered_after_retire_does_not_block(self):
        # 退休之后才进入的读者不可能看见旧对象，不应阻碍回收
        destroyed = []
        mgr = ReclaimManager()
        h = mgr.create("v", on_destroy=destroyed.append)
        h.release()
        with mgr.reader():
            self.assertEqual(mgr.reclaim(), 1)
        self.assertEqual(destroyed, ["v"])

    def test_destroy_callback_exception_does_not_lose_queue(self):
        def bad(_):
            raise RuntimeError("boom")

        mgr = ReclaimManager()
        h1 = mgr.create("bad", on_destroy=bad)
        h2 = mgr.create("good")
        h1.release()
        h2.release()
        with self.assertRaises(RuntimeError):
            mgr.reclaim()
        # 出问题的对象已从队列移除，其余对象不受影响
        self.assertEqual(mgr.pending_count, 0)
        self.assertTrue(h2._obj.destroyed)

    def test_many_objects_fifo_pending_queue(self):
        mgr = ReclaimManager()
        order = []
        handles = [mgr.create(i, on_destroy=order.append) for i in range(100)]
        for h in handles:
            h.release()
        self.assertEqual(mgr.pending_count, 100)
        self.assertEqual(mgr.reclaim(), 100)
        self.assertEqual(order, list(range(100)))  # 按退休顺序回收

    def test_handle_usable_while_other_handles_released(self):
        mgr = ReclaimManager()
        h1 = mgr.create("v")
        h2 = h1._obj.acquire()
        h1.release()
        self.assertEqual(h2.get(), "v")  # 剩余句柄不受影响
        h2.release()
        mgr.reclaim()

    def test_reclaim_is_idempotent(self):
        mgr = ReclaimManager()
        h = mgr.create("v")
        h.release()
        self.assertEqual(mgr.reclaim(), 1)
        self.assertEqual(mgr.reclaim(), 0)
        self.assertEqual(mgr.reclaim(), 0)

    def test_background_reclaimer_double_start_stop(self):
        mgr = ReclaimManager()
        mgr.start_background_reclaimer(interval=0.005)
        mgr.start_background_reclaimer(interval=0.005)  # 幂等
        h = mgr.create("v")
        h.release()
        mgr.stop_background_reclaimer()
        mgr.stop_background_reclaimer()                 # 幂等
        self.assertTrue(h._obj.destroyed)

    def test_cross_thread_handle_release(self):
        # 句柄在创建它的线程之外的线程中释放也必须正确
        mgr = ReclaimManager()
        h = mgr.create("v")
        done = threading.Event()

        def releaser():
            h.release()
            done.set()

        t = threading.Thread(target=releaser)
        t.start()
        self.assertTrue(done.wait(timeout=5))
        t.join(timeout=5)
        self.assertTrue(h._obj.retired)
        self.assertEqual(mgr.reclaim(), 1)
        with self.assertRaises(UseAfterReleaseError):
            h.get()

    def test_object_retired_error_is_use_after_release(self):
        mgr = ReclaimManager()
        h = mgr.create("v")
        obj = h._obj
        h.release()
        with self.assertRaises(UseAfterReleaseError):  # ObjectRetiredError 是其子类
            obj.acquire()
        mgr.reclaim()


if __name__ == "__main__":
    unittest.main(verbosity=2)
