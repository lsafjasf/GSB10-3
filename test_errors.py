"""错误检测测试：计数溢出 / 重复释放 / 释放后继续使用。运行: python3 test_errors.py -v"""

import unittest

from refcount import (
    DoubleReleaseError,
    ObjectRetiredError,
    ReclaimManager,
    RefCountOverflowError,
    UseAfterReleaseError,
)


class TestRefCountOverflow(unittest.TestCase):
    def test_overflow_raises_and_count_stays_at_limit(self):
        mgr = ReclaimManager(max_refcount=4)
        handles = [mgr.create("v")]
        for _ in range(3):
            handles.append(handles[0]._obj.acquire())
        self.assertEqual(handles[0]._obj.refcount, 4)
        with self.assertRaises(RefCountOverflowError):
            handles[0]._obj.acquire()
        # 溢出后计数不被破坏，仍可正常释放
        self.assertEqual(handles[0]._obj.refcount, 4)
        for h in handles:
            h.release()
        self.assertEqual(mgr.reclaim(), 1)

    def test_default_limit_is_31bit(self):
        mgr = ReclaimManager()
        h = mgr.create("v")
        self.assertEqual(h._obj._max_refcount, (1 << 31) - 1)
        h.release()
        mgr.reclaim()


class TestDoubleRelease(unittest.TestCase):
    def test_same_handle_released_twice(self):
        mgr = ReclaimManager()
        h = mgr.create("v")
        h.release()
        with self.assertRaises(DoubleReleaseError):
            h.release()
        self.assertEqual(mgr.reclaim(), 1)

    def test_double_release_does_not_corrupt_count(self):
        mgr = ReclaimManager()
        h1 = mgr.create("v")
        h2 = h1._obj.acquire()      # 计数 = 2
        h1.release()                # 计数 = 1
        with self.assertRaises(DoubleReleaseError):
            h1.release()            # 被拦截，计数不变
        self.assertEqual(h2._obj.refcount, 1)
        self.assertFalse(h2._obj.retired)
        h2.release()
        self.assertEqual(mgr.reclaim(), 1)

    def test_context_manager_exit_after_manual_release(self):
        mgr = ReclaimManager()
        h = mgr.create("v")
        h.release()
        with self.assertRaises(DoubleReleaseError):
            with h:                 # __exit__ 会再次 release
                pass
        mgr.reclaim()


class TestUseAfterRelease(unittest.TestCase):
    def test_get_after_handle_release(self):
        mgr = ReclaimManager()
        h = mgr.create("v")
        h.release()
        with self.assertRaises(UseAfterReleaseError):
            h.get()
        mgr.reclaim()

    def test_get_after_object_destroyed(self):
        mgr = ReclaimManager()
        h1 = mgr.create("v")
        h2 = h1._obj.acquire()
        h1.release()
        h2.release()
        mgr.reclaim()               # 真正析构
        with self.assertRaises(UseAfterReleaseError):
            h1.get()

    def test_acquire_after_retire(self):
        mgr = ReclaimManager()
        h = mgr.create("v")
        obj = h._obj
        h.release()                 # 计数归零 -> 退休
        with self.assertRaises(ObjectRetiredError):
            obj.acquire()
        mgr.reclaim()

    def test_peek_after_destroy(self):
        mgr = ReclaimManager()
        h = mgr.create("v")
        obj = h._obj
        h.release()
        mgr.reclaim()
        with mgr.reader():
            with self.assertRaises(UseAfterReleaseError):
                obj.peek()


if __name__ == "__main__":
    unittest.main(verbosity=2)
