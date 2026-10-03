"""Registry：RCU 风格读优化查找，以及读写混合压力下的回收正确性。"""

import os
import random
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rcuref import Domain, ReadLockRequiredError, Registry


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.dom = Domain()
        self.reg = Registry(self.dom)

    def tearDown(self):
        self.reg.remove_all()
        self.dom.drain(timeout=5)

    def test_lookup_requires_read_lock(self):
        obj = self.dom.register("v1")
        self.reg.publish("k", obj)
        with self.assertRaises(ReadLockRequiredError):
            self.reg.lookup("k")
        with self.dom.read_lock():
            self.assertIs(self.reg.lookup("k"), obj)

    def test_reader_sees_consistent_object_while_replaced(self):
        destroyed = []
        old = self.dom.register("old", reclaim=lambda o: destroyed.append(o))
        self.reg.publish("k", old)
        entered = threading.Event()
        leave = threading.Event()
        seen = []

        def reader():
            with self.dom.read_lock():
                obj = self.reg.lookup("k")
                seen.append(obj.payload)
                entered.set()
                leave.wait(10)
                # 临界区末尾旧对象必须仍然可用
                seen.append(obj.payload)

        t = threading.Thread(target=reader)
        t.start()
        self.assertTrue(entered.wait(5))

        new = self.dom.register("new")
        self.reg.publish("k", new)  # 替换：旧对象退役
        self.assertEqual(old.state, "RETIRED")
        self.assertEqual(destroyed, [])

        leave.set()
        t.join(5)
        self.assertEqual(seen, ["old", "old"])
        self.dom.drain(timeout=5)
        self.assertEqual(destroyed, [old])
        with self.dom.read_lock():
            self.assertIs(self.reg.lookup("k"), new)

    def test_remove_retires_object(self):
        obj = self.dom.register("v")
        self.reg.publish("k", obj)
        removed = self.reg.remove("k")
        self.assertIs(removed, obj)
        self.assertEqual(obj.state, "RETIRED")
        with self.dom.read_lock():
            self.assertIsNone(self.reg.lookup("k"))
        self.assertIsNone(self.reg.remove("missing"))


class MixedTrafficTest(unittest.TestCase):
    """读方高频 lookup + 写方高频替换：无异常、无泄漏、无提前回收。"""

    def test_mixed_read_write_stress(self):
        dom = Domain()
        reg = Registry(dom)
        reclaimed = []
        counter_lock = threading.Lock()

        def on_reclaim(obj):
            with counter_lock:
                reclaimed.append(obj)

        for i in range(8):
            reg.publish(i, dom.register(("v", i), reclaim=on_reclaim))

        stop = threading.Event()
        errors = []

        def reader():
            try:
                while not stop.is_set():
                    with dom.read_lock():
                        obj = reg.lookup(random.randrange(8))
                        if obj is not None:
                            _ = obj.payload  # 临界区内访问必须安全
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        def writer():
            try:
                for n in range(300):
                    key = random.randrange(8)
                    reg.publish(
                        key, dom.register(("v", key, n), reclaim=on_reclaim)
                    )
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        readers = [threading.Thread(target=reader) for _ in range(4)]
        writers = [threading.Thread(target=writer) for _ in range(2)]
        for t in readers + writers:
            t.start()
        for t in writers:
            t.join(30)
        stop.set()
        for t in readers:
            t.join(30)
        self.assertFalse(any(t.is_alive() for t in readers + writers))
        self.assertEqual(errors, [])

        reg.remove_all()
        dom.drain(timeout=10)
        self.assertEqual(dom.pending_count, 0)
        # 发布过的对象总数 = 初始 8 + 写方 2*300；全部应被回收
        self.assertEqual(len(reclaimed), 8 + 600)


if __name__ == "__main__":
    unittest.main(verbosity=2)
