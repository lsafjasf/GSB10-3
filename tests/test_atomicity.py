"""原子性断言。

两类断言：
1) 确定性断言（barrier）：在切换“提交点”处把写线程挂住，挂住期间反复读取，
   断言每次读到的都是完整旧版本；放行后读到完整新版本，没有任何半新半旧。
2) 压力断言：多个读线程在大量反复热加载期间持续读取，断言每次读到的
   字段组合都精确等于某一个完整版本，绝不出现跨版本拼凑。

附负对照：同样的断言作用在“逐字段发布”的错误实现上时必须失败，
证明该断言真的能抓住半新半旧，而不是永远通过。
"""

import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hotreload import ConfigStore, ValidationError


def cfg(service, port, features, qps):
    return {
        "service_name": service,
        "port": port,
        "timeout_ms": 500,
        "features": list(features),
        "rate_limit": {"qps": qps, "burst": qps * 2},
    }


def fingerprint(snapshot):
    """一次读取得到的全部关键内容；只允许等于某一个完整版本的指纹。"""
    d = snapshot.data
    return (
        snapshot.version,
        d["service_name"],
        d["port"],
        tuple(d["features"]),
        d["rate_limit"]["qps"],
    )


class BarrierStore(ConfigStore):
    """在引用赋值之前阻塞，模拟“切换进行中”的确定性时间窗。"""

    def __init__(self, initial):
        super().__init__(initial)
        self.barrier = None

    def _before_commit(self, snapshot):
        if self.barrier is not None:
            self.barrier()


class TestDeterministicMidSwitchReads(unittest.TestCase):
    def test_readers_never_see_half_published_config(self):
        store = BarrierStore(cfg("svc-old", 1000, ["read"], 10))
        old_fp = fingerprint(store.current)
        new_raw = cfg("svc-new", 2000, ["read", "admin"], 99)

        release = threading.Event()
        started = threading.Event()
        store.barrier = lambda: (started.set(), release.wait(5))

        seen_during = []
        load_error = []

        def loader():
            try:
                store.load(new_raw)
            except Exception as exc:  # pragma: no cover
                load_error.append(exc)

        t = threading.Thread(target=loader)
        t.start()
        self.assertTrue(started.wait(2), "writer never reached commit point")

        # 写线程此刻正停在“提交前”的窗口里。
        for _ in range(10_000):
            fp = fingerprint(store.current)
            seen_during.append(fp)
            self.assertEqual(fp, old_fp, f"torn read during switch: {fp}")

        release.set()
        t.join(2)
        self.assertEqual(load_error, [])
        new_fp = fingerprint(store.current)
        self.assertNotEqual(new_fp, old_fp)
        self.assertEqual(new_fp[1:], ("svc-new", 2000, ("read", "admin"), 99))
        # 窗口内确实读到过东西，断言有效
        self.assertEqual(len(seen_during), 10_000)

    def test_barrier_holds_writer_lock_and_blocks_next_load(self):
        # 切换串行化：上一个切换没提交完，下一个 load 不能插进来。
        store = BarrierStore(cfg("svc-1", 1, [], 1))
        release = threading.Event()
        started = threading.Event()
        store.barrier = lambda: (started.set(), release.wait(5))

        t = threading.Thread(target=lambda: store.load(cfg("svc-2", 2, [], 2)))
        t.start()
        started.wait(2)

        second_done = threading.Event()
        t2 = threading.Thread(
            target=lambda: (store.load(cfg("svc-3", 3, [], 3)), second_done.set())
        )
        t2.start()
        self.assertFalse(second_done.wait(0.2), "second load leaked into in-flight switch")
        release.set()
        t.join(2)
        self.assertTrue(second_done.wait(2))
        t2.join(2)
        self.assertEqual(store.get("service_name"), "svc-3")


class TestConcurrentReadsAcrossReloads(unittest.TestCase):
    def test_stress_readers_see_only_whole_versions(self):
        configs = [
            cfg("svc-a", 1001, ["read"], 100),
            cfg("svc-b", 2002, ["read", "write"], 200),
            cfg("svc-c", 3003, ["admin"], 300),
        ]
        store = ConfigStore(configs[0])
        n_valid_loads = 300
        # 合法加载序列完全确定：v(k) 对应 configs[(k-1) % 3]。
        # 预算每一个可能版本的完整指纹，读取结果必须恰好落在这个集合里。
        allowed = set()
        for v in range(1, n_valid_loads + 2):
            raw = configs[(v - 1) % len(configs)]
            allowed.add((v, raw["service_name"], raw["port"],
                         tuple(raw["features"]), raw["rate_limit"]["qps"]))

        stop = threading.Event()
        observed = set()
        torn = []
        reads = [0]

        def reader():
            local = 0
            while not stop.is_set():
                fp = fingerprint(store.current)
                local += 1
                if fp not in allowed:
                    torn.append(fp)
                observed.add(fp)
            reads[0] += local

        def writer():
            idx = 1
            for _ in range(n_valid_loads):
                store.load(configs[idx % len(configs)])
                idx += 1
                # 穿插非法配置：必须被拒绝，不产生新版本
                try:
                    store.load(cfg("bad", -1, ["nope"], -1))
                except ValidationError:
                    pass

        readers = [threading.Thread(target=reader) for _ in range(4)]
        for r in readers:
            r.start()
        w = threading.Thread(target=writer)
        w.start()
        w.join(30)
        time.sleep(0.1)  # 让读线程多观察一会儿稳定状态
        stop.set()
        for r in readers:
            r.join(5)

        self.assertFalse(torn, f"{len(torn)} torn reads, e.g. {torn[:3]}")
        self.assertGreater(reads[0], 100_000, f"only {reads[0]} reads observed")
        # 至少实际看到两个不同版本，证明读取确实跨越了切换
        self.assertGreater(len(observed), 1)
        # 非法穿插加载从未产生过新版本
        self.assertTrue(all(fp in allowed for fp in observed))
        self.assertEqual(store.current.version, n_valid_loads + 1)
        # 回滚历史完整：300 次生效都可逐版回退
        self.assertEqual(len(store.history()), n_valid_loads + 1)


class TestFieldByFieldNegativeControl(unittest.TestCase):
    """负对照：证明上面的不变量断言能抓住“逐字段发布”的错误实现。"""

    def test_assertion_catches_torn_state(self):
        old = {"service": "old", "port": 1, "qps": 10}
        new = {"service": "new", "port": 2, "qps": 20}
        allowed = {tuple(sorted(old.items())), tuple(sorted(new.items()))}

        class BadFieldByFieldStore:
            def __init__(self):
                self.data = dict(old)
                self.release = threading.Event()
                self.started = threading.Event()
                self.torn = None

            def switch(self):
                self.data["service"] = new["service"]   # 只改第一个字段……
                self.started.set()
                self.release.wait(5)
                self.data["port"] = new["port"]         # ……窗口之后再改其余字段
                self.data["qps"] = new["qps"]

            def read(self):
                return tuple(sorted(self.data.items()))

        bad = BadFieldByFieldStore()
        t = threading.Thread(target=bad.switch)
        t.start()
        bad.started.wait(2)
        observed = bad.read()
        bad.release.set()
        t.join(2)

        # 逐字段发布会读到 service=new 而 port=1 的拼凑状态
        self.assertNotIn(observed, allowed,
                         "negative control failed: torn state looked whole")
        self.assertEqual(dict(observed)["service"], "new")
        self.assertEqual(dict(observed)["port"], 1)


if __name__ == "__main__":
    unittest.main()
