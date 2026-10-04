"""回滚：生效与失效都能撤销，断言回滚前后的版本号与读取结果。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hotreload import ConfigStore, ValidationError


def cfg(port, qps=100):
    return {
        "service_name": f"svc-{port}",
        "port": port,
        "timeout_ms": 500,
        "features": ["read"],
        "rate_limit": {"qps": qps, "burst": qps * 2},
    }


class TestRollback(unittest.TestCase):
    def setUp(self):
        self.store = ConfigStore(cfg(1000))          # v1
        self.store.load(cfg(2000))                   # v2
        self.store.load(cfg(3000))                   # v3

    def test_rollback_restores_previous_version_and_reads(self):
        self.assertEqual(self.store.current.version, 3)
        self.assertEqual(self.store.get("port"), 3000)

        rolled = self.store.rollback()               # 失效 v3
        self.assertEqual(rolled.version, 2)
        self.assertEqual(self.store.current.version, 2)
        self.assertEqual(self.store.get("port"), 2000)
        self.assertEqual(self.store.get("service_name"), "svc-2000")

        rolled = self.store.rollback()               # 失效 v2
        self.assertEqual(rolled.version, 1)
        self.assertEqual(self.store.get("port"), 1000)

    def test_rollback_at_oldest_version_is_noop(self):
        self.store.rollback()
        self.store.rollback()
        self.assertEqual(self.store.current.version, 1)
        self.assertIsNone(self.store.rollback())     # 没有更早的版本
        self.assertEqual(self.store.current.version, 1)
        self.assertEqual(self.store.get("port"), 1000)

    def test_redo_reapplies_rolled_back_version(self):
        self.store.rollback()                        # v3 -> v2
        self.assertEqual(self.store.get("port"), 2000)
        redone = self.store.redo()                   # 撤销这次失效
        self.assertEqual(redone.version, 3)
        self.assertEqual(self.store.get("port"), 3000)

    def test_rollback_redo_round_trip_is_stable(self):
        for _ in range(3):
            self.store.rollback()                    # v3 -> v2
            self.assertEqual(self.store.get("port"), 2000)
            self.store.redo()                        # v2 -> v3
            self.assertEqual(self.store.get("port"), 3000)
        self.assertEqual(self.store.current.version, 3)

    def test_new_load_invalidates_redo_stack(self):
        self.store.rollback()                        # v3 -> v2，redo=[v3]
        self.assertTrue(self.store.can_redo)
        self.store.load(cfg(4000))                   # 新分支 v4
        self.assertFalse(self.store.can_redo)
        self.assertIsNone(self.store.redo())
        self.assertEqual(self.store.current.version, 4)
        self.assertEqual(self.store.get("port"), 4000)
        # 回滚沿新分支走：v4 -> v2 -> v1，不会跳回被放弃的 v3
        self.assertEqual(self.store.rollback().version, 2)
        self.assertEqual(self.store.rollback().version, 1)
        self.assertIsNone(self.store.rollback())

    def test_failed_load_does_not_create_rollback_entry(self):
        with self.assertRaises(ValidationError):
            self.store.load(cfg(-1))
        self.assertEqual(self.store.history(), (1, 2, 3))
        self.assertEqual(self.store.rollback().version, 2)

    def test_rollback_is_atomic_snapshot_swap(self):
        # 回滚同样是整体快照切换：读到的字段组合永远属于某一个完整版本
        v3 = self.store.current
        self.store.rollback()
        cur = self.store.current
        self.assertEqual((cur.version, cur.get("port"), cur.get("service_name")),
                         (2, 2000, "svc-2000"))
        self.assertEqual(v3.get("port"), 3000)       # 旧快照对象未被改动


if __name__ == "__main__":
    unittest.main()
