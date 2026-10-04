"""sensitive_data 的单元测试（仅标准库 unittest）。

运行：python3 -m unittest test_sensitive_data -v
"""

import gc
import os
import unittest
import warnings

from sensitive_data import (
    LeakScanner,
    SecretBuffer,
    SecretDestroyedError,
    SecretError,
    SecretInUseError,
)


def fresh_secret(n: int = 32) -> bytes:
    """运行时随机生成明文，避免测试代码自身成为扫描噪声。"""
    return os.urandom(n)


class NormalUseTest(unittest.TestCase):
    """情形一：正常使用。"""

    def test_read_write_through_view(self):
        data = fresh_secret()
        secret = SecretBuffer(data)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            with secret.access() as view:
                self.assertEqual(bytes(view), data)          # 读
                view[0] ^= 0xFF                               # 写
                self.assertNotEqual(bytes(view), data)
        secret.destroy()
        self.assertTrue(secret.destroyed)

    def test_str_and_bytearray_input(self):
        secret = SecretBuffer("口令-秘密")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            with secret.access() as view:
                self.assertEqual(bytes(view), "口令-秘密".encode("utf-8"))
        secret.destroy()

    def test_no_implicit_copy(self):
        secret = SecretBuffer(fresh_secret())
        with self.assertRaises(SecretError):
            bytes(secret)
        secret.destroy()

    def test_escaped_view_warns(self):
        secret = SecretBuffer(fresh_secret())
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with secret.access() as view:
                escaped = view  # 故意让 view 逃逸出会话
            self.assertTrue(any(issubclass(w.category, ResourceWarning) for w in caught))
        self.assertEqual(secret.escaped_view_count(), 1)
        del escaped, view
        gc.collect()
        self.assertEqual(secret.escaped_view_count(), 0)
        secret.destroy()


class DestroyTest(unittest.TestCase):
    """销毁语义：逐字节清零 + 幂等。"""

    def test_buffer_zeroed_byte_by_byte(self):
        data = fresh_secret(64)
        secret = SecretBuffer(data)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            with secret.access() as view:
                before = bytes(view)
        secret.destroy()
        # 销毁后底层缓冲区已脱钩，但销毁前的 memoryview 仍指向同一块内存，
        # 可用来验证每一个字节都被覆写为 0。
        self.assertTrue(all(b == 0 for b in view), "存在未清零的字节")
        self.assertEqual(bytes(view), b"\x00" * 64)
        self.assertNotEqual(before, bytes(view))

    def test_destroy_is_idempotent(self):
        secret = SecretBuffer(fresh_secret())
        secret.destroy()
        for _ in range(5):                      # 重复销毁不得报错
            secret.destroy()
        self.assertTrue(secret.destroyed)       # 结果不变
        with self.assertRaises(SecretDestroyedError):
            secret.access()                     # 状态语义也未被改变

    def test_double_destroy_keeps_zero(self):
        secret = SecretBuffer(fresh_secret(16))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            with secret.access() as view:
                pass
        secret.destroy()
        snapshot = bytes(view)
        secret.destroy()
        secret.destroy()
        self.assertEqual(bytes(view), snapshot)          # 重复销毁不改变结果
        self.assertEqual(snapshot, b"\x00" * 16)

    def test_early_destroy_requires_force(self):
        secret = SecretBuffer(fresh_secret())
        with self.assertRaises(SecretInUseError):
            with secret.access():
                secret.destroy()                # 会话进行中，拒绝销毁
        # 提前销毁：force
        secret2 = SecretBuffer(fresh_secret(16))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            with secret2.access() as view:
                secret2.destroy(force=True)
                self.assertEqual(bytes(view), b"\x00" * 16)  # 使用中被清零
        with self.assertRaises(SecretDestroyedError):
            secret2.access()


class PostDestroyAccessTest(unittest.TestCase):
    """情形：销毁后继续访问。"""

    def test_access_after_destroy_raises(self):
        secret = SecretBuffer(fresh_secret())
        secret.destroy()
        with self.assertRaises(SecretDestroyedError):
            secret.access()
        with self.assertRaises(SecretDestroyedError):
            with secret.access():
                pass
        with self.assertRaises(SecretDestroyedError):
            len(secret)
        with self.assertRaises(SecretError):
            bytes(secret)


class LeakDetectionTest(unittest.TestCase):
    """使用期外的意外复制：拷贝计数与告警。"""

    def test_no_leak_in_normal_use(self):
        secret = SecretBuffer(fresh_secret())
        with secret.access() as view:
            view[0] ^= 1
            del view  # 用完即弃，不把 memoryview 逃逸出会话
        report = secret.audit()
        self.assertFalse(report.leaked, report.summary())
        self.assertEqual(report.copy_count, 0)
        secret.destroy()

    def test_copy_detected_and_counted(self):
        secret = SecretBuffer(fresh_secret())
        with secret.access() as view:
            leaked_copy = bytes(view)                # 意外复制到 bytes（不可清零）
            leaked_text = leaked_copy.decode("latin1")  # 意外复制到 str
            del view
        report = secret.audit()
        self.assertTrue(report.leaked)
        self.assertEqual(report.copy_count, 2, report.summary())
        types = {f.type_name for f in report.findings}
        self.assertEqual(types, {"bytes", "str"})
        # 告警结果
        with self.assertWarns(UserWarning):
            if report.leaked:
                warnings.warn(f"敏感数据泄漏：{report.summary()}", UserWarning)
        del leaked_copy, leaked_text
        secret.destroy()

    def test_nulling_variable_is_not_enough(self):
        """光把变量置空/重绑定，并不能让已经存在的副本消失。"""
        # 模拟：某处把明文转成了不可清零的 str（如日志、异常消息）
        token = "marker-" + os.urandom(8).hex()
        holder = SecretBuffer(token)
        snapshot = token          # 意外复制：另一变量引用同一 str
        holder.destroy()
        token = None              # 把变量置空……
        report = LeakScanner(snapshot.encode()).scan()
        # ……但 snapshot 仍持有明文，副本依然存在于解释器中
        self.assertGreaterEqual(report.copy_count, 1)
        self.assertIn("str", {f.type_name for f in report.findings})
        del snapshot

    def test_standalone_scanner(self):
        data = fresh_secret()
        # bytes(data) 对 bytes 返回同一对象不算复制，显式制造两份真副本
        copies = [bytes(bytearray(data)), bytearray(data)]
        scanner = LeakScanner(data)
        report = scanner.scan()
        self.assertEqual(report.copy_count, 2, report.summary())
        for f in report.findings:
            self.assertEqual(f.offset, 0)
            self.assertEqual(f.obj_len, len(data))
        del copies
        gc.collect()
        report2 = LeakScanner(data).scan()
        self.assertEqual(report2.copy_count, 0)
        del data


class EdgeCaseTest(unittest.TestCase):
    """边界用例。"""

    def setUp(self):
        # 本类用例会在会话外继续持有 memoryview 以验证清零，屏蔽逃逸告警
        self._w = warnings.catch_warnings()
        self._w.__enter__()
        warnings.simplefilter("ignore", ResourceWarning)
        self.addCleanup(self._w.__exit__, None, None, None)

    def test_empty_buffer(self):
        secret = SecretBuffer(b"")
        with secret.access() as view:
            self.assertEqual(len(view), 0)
        secret.destroy()
        secret.destroy()
        with self.assertRaises(SecretDestroyedError):
            secret.access()

    def test_one_byte_buffer(self):
        secret = SecretBuffer(b"\x41")
        with secret.access() as view:
            self.assertEqual(bytes(view), b"A")
        secret.destroy()
        self.assertEqual(bytes(view), b"\x00")

    def test_large_buffer_all_zeroed(self):
        n = 1 << 20  # 1 MiB
        secret = SecretBuffer(os.urandom(n))
        with secret.access() as view:
            pass
        secret.destroy()
        self.assertEqual(view.tobytes().count(0), n)

    def test_binary_zero_and_ff_bytes(self):
        data = bytes(range(256)) * 4
        secret = SecretBuffer(data)
        with secret.access() as view:
            self.assertEqual(bytes(view), data)
        secret.destroy()
        self.assertTrue(all(b == 0 for b in view))

    def test_scanner_rejects_empty_pattern(self):
        with self.assertRaises(ValueError):
            LeakScanner(b"")

    def test_audit_after_destroy_is_clean(self):
        secret = SecretBuffer(fresh_secret())
        secret.destroy()
        report = secret.audit()
        self.assertFalse(report.leaked)


if __name__ == "__main__":
    unittest.main(verbosity=2)
