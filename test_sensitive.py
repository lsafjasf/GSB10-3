"""test_sensitive.py — SensitiveBytes 自测（unittest，仅标准库）。

覆盖：正常使用、提前销毁、重复销毁（幂等）、销毁后继续访问、
使用窗口外的意外复制检测、销毁时存活副本告警。
运行：python3 test_sensitive.py -v
"""

import gc
import unittest
import warnings

from sensitive import (
    DestroyedError,
    LiveCopiesWarning,
    SensitiveBytes,
    UnexpectedCopyWarning,
)

SECRET = b"s3cr3t-token-0123456789"


class TestNormalUse(unittest.TestCase):
    def test_normal_use_and_zeroing(self):
        s = SensitiveBytes(SECRET)
        before = s.raw_snapshot()
        self.assertEqual(before, SECRET)

        with s.use() as view:
            self.assertEqual(bytes(view), SECRET)  # 窗口内可读明文

        self.assertTrue(s.destroy())               # 首次销毁生效
        after = s.raw_snapshot()
        self.assertEqual(after, b"\x00" * len(SECRET))   # 逐字节为零
        self.assertTrue(s.verify_zeroed())
        self.assertNotEqual(before, after)
        print(f"\n[清零验证] before={before.hex()}")
        print(f"[清零验证] after ={after.hex()}")

    def test_copy_inside_window_counted_no_warning(self):
        s = SensitiveBytes(SECRET)
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # 窗口内复制不应告警
            with s.use():
                dup = s.copy()
            self.assertEqual(dup.bytes(), SECRET)
        self.assertEqual(s.copy_count, 1)
        self.assertEqual(s.unexpected_copy_count, 0)
        del dup
        s.destroy()


class TestEarlyDestroy(unittest.TestCase):
    def test_destroy_before_any_use(self):
        s = SensitiveBytes(SECRET)
        self.assertTrue(s.destroy())
        self.assertTrue(s.verify_zeroed())
        with self.assertRaises(DestroyedError):
            with s.use():
                pass


class TestIdempotentDestroy(unittest.TestCase):
    def test_repeated_destroy_is_noop(self):
        s = SensitiveBytes(SECRET)
        self.assertTrue(s.destroy())    # 第一次：生效
        snapshot1 = s.raw_snapshot()
        self.assertFalse(s.destroy())   # 第二次：no-op，不报错
        self.assertFalse(s.destroy())   # 第三次：同样 no-op
        snapshot2 = s.raw_snapshot()
        # 幂等断言：重复销毁不改变结果，缓冲区保持全零
        self.assertEqual(snapshot1, snapshot2)
        self.assertEqual(snapshot2, b"\x00" * len(SECRET))
        self.assertTrue(s.verify_zeroed())


class TestAccessAfterDestroy(unittest.TestCase):
    def test_use_after_destroy_raises(self):
        s = SensitiveBytes(SECRET)
        s.destroy()
        with self.assertRaises(DestroyedError):
            with s.use():
                pass

    def test_copy_after_destroy_raises_and_warns(self):
        s = SensitiveBytes(SECRET)
        s.destroy()
        with self.assertWarns(UnexpectedCopyWarning):
            with self.assertRaises(DestroyedError):
                s.copy()
        # 销毁后的复制尝试计入意外复制，但不计入成功拷贝
        self.assertEqual(s.unexpected_copy_count, 1)
        self.assertEqual(s.copy_count, 0)


class TestUnexpectedCopyDetection(unittest.TestCase):
    def test_copy_outside_window_warns_and_counts(self):
        s = SensitiveBytes(SECRET)
        with self.assertWarns(UnexpectedCopyWarning):
            dup = s.copy()              # 窗口外复制 → 告警
        self.assertEqual(dup.bytes(), SECRET)
        self.assertEqual(s.copy_count, 1)
        self.assertEqual(s.unexpected_copy_count, 1)
        print(f"\n[拷贝计数] total={s.copy_count} "
              f"unexpected={s.unexpected_copy_count}")
        del dup
        s.destroy()

    def test_live_copies_at_destroy_warn(self):
        s = SensitiveBytes(SECRET)
        with s.use():
            dup1 = s.copy()
            dup2 = s.copy()
        self.assertEqual(s.live_copy_count, 2)
        with self.assertWarns(LiveCopiesWarning) as cm:
            s.destroy()
        self.assertIn("2 live copy", str(cm.warning))
        # 容器已清零，但泄漏的副本仍持有明文 → 正是告警的意义
        self.assertTrue(s.verify_zeroed())
        self.assertEqual(dup1.bytes(), SECRET)
        print(f"[告警结果] {cm.warning}")
        del dup1, dup2
        gc.collect()

    def test_no_live_copies_no_warning(self):
        s = SensitiveBytes(SECRET)
        with s.use():
            dup = s.copy()
        del dup
        gc.collect()
        self.assertEqual(s.live_copy_count, 0)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            s.destroy()                 # 无存活副本 → 不告警


if __name__ == "__main__":
    unittest.main(verbosity=2)
