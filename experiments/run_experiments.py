#!/usr/bin/env python3
"""插件框架生命周期的可执行实验（Python 3 标准库，无第三方依赖）。

运行方式（在仓库根目录）：
    python3 experiments/run_experiments.py -v

每个实验打印 OBSERVED 行记录实际观察到的行为，并用断言固化观察结果。
实验只调用被测代码（sut/）的公开行为，不修改 sut/ 下任何文件。
"""

import os
import sys
import threading
import unittest

SUT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sut")
sys.path.insert(0, os.path.abspath(SUT_ROOT))

from pluginfw import BUS, REGISTRY, PluginManager, lifecycle  # noqa: E402
from pluginfw.errors import PluginInitError  # noqa: E402
from pluginfw.lifecycle import IllegalTransition  # noqa: E402

PLUGIN_DIR = os.path.join(os.path.abspath(SUT_ROOT), "plugins")


def observed(msg):
    print(f"OBSERVED: {msg}", flush=True)


def reset_framework():
    """测试夹具清理（只动全局单例与 sys.modules，不动 sut/ 源码）。"""
    for name in REGISTRY.names():
        REGISTRY.remove(name)
    BUS._subs.clear()
    for mod in [m for m in list(sys.modules) if m.startswith("plugins.")]:
        del sys.modules[mod]


class LifecycleExperiments(unittest.TestCase):
    def setUp(self):
        reset_framework()
        self.mgr = PluginManager(PLUGIN_DIR)

    def tearDown(self):
        reset_framework()

    # E0: 加载后、初始化前，插件就可以被调用
    def test_e0_call_before_init_is_dispatched(self):
        self.mgr.load_plugin("alpha")
        plugin = REGISTRY.get("alpha")
        observed(f"load 后未 init：REGISTRY 已可见，state={plugin.state!r}")
        self.assertEqual(plugin.state, lifecycle.LOADED)
        with self.assertRaises(RuntimeError) as ctx:
            self.mgr.call("alpha", "work")
        observed(f"call('alpha','work') 在 init 前被照常分发，"
                 f"靠插件自己的防御检查才抛出: {ctx.exception}")
        # call() 本身没有任何状态检查；若插件不做防御（见 E2 的 beta），
        # 暴露的就是更隐蔽的 AttributeError。

    # E1: 重复加载不是幂等的，旧实例成为“幽灵订阅者”
    def test_e1_duplicate_load_leaks_subscriptions(self):
        first = self.mgr.load_plugin("alpha")
        self.mgr.initialize_plugin("alpha")
        BUS.publish("tick", 1)
        second = self.mgr.load_plugin("alpha")  # 重复加载：无去重
        observed(f"重复 load 返回新实例：first is second = {first is second}，"
                 f"注册表条目被覆盖：REGISTRY.get('alpha') is second = "
                 f"{REGISTRY.get('alpha') is second}")
        self.assertIsNot(first, second)
        self.assertIs(REGISTRY.get("alpha"), second)

        self.mgr.initialize_plugin("alpha")  # 初始化的是 second
        delivered = BUS.publish("tick", 2)
        observed(f"两个实例都收到事件：delivered={delivered}，"
                 f"first.received={first.received}，second.received={second.received}")
        self.assertEqual(delivered, 2)
        self.assertEqual(first.received, [1, 2])
        self.assertEqual(second.received, [2])

        self.mgr.unload_plugin("alpha")  # 只对注册表里的 second 做 teardown
        delivered = BUS.publish("tick", 3)
        observed(f"卸载后事件仍被投递给幽灵实例：delivered={delivered}，"
                 f"first.received={first.received}")
        self.assertEqual(delivered, 1)
        self.assertEqual(first.received, [1, 2, 3])

    # E2: 加载（初始化）失败后，插件仍留在注册表、仍可被调用，且无法直接重试
    def test_e2_failed_init_stays_callable_and_unretryable(self):
        self.mgr.load_plugin("beta")
        with self.assertRaises(PluginInitError):
            self.mgr.initialize_plugin("beta")
        beta = REGISTRY.get("beta")
        observed(f"init 失败后：REGISTRY.get('beta') is None = {beta is None}，"
                 f"state={beta.state!r}")
        self.assertIsNotNone(beta)
        self.assertEqual(beta.state, lifecycle.FAILED)

        with self.assertRaises(AttributeError) as ctx:
            self.mgr.call("beta", "work")
        observed(f"FAILED 状态下 call 仍被分发，踩到未初始化属性: {ctx.exception}")

        with self.assertRaises(IllegalTransition) as ctx:
            self.mgr.initialize_plugin("beta")  # FAILED -> INITIALIZED 不在转移表里
        observed(f"失败后直接重试 init 被拒绝: {ctx.exception}")

        # 瞬时失败也无法恢复：第二次 init() 本身成功，但状态转移失败
        self.mgr.load_plugin("delta")
        with self.assertRaises(PluginInitError):
            self.mgr.initialize_plugin("delta")
        delta = REGISTRY.get("delta")
        with self.assertRaises(IllegalTransition) as ctx:
            self.mgr.initialize_plugin("delta")
        observed(f"瞬时故障后重试：init() 已成功 ready={delta.ready}，"
                 f"状态却卡在 {delta.state!r}，转移被拒绝: {ctx.exception}")
        self.assertTrue(delta.ready)
        self.assertEqual(delta.state, lifecycle.FAILED)

    # E3: 卸载后再加载，拿到的是同一个模块对象，模块级状态残留
    def test_e3_reload_after_unload_keeps_module_state(self):
        self.mgr.load_plugin("alpha")
        self.mgr.initialize_plugin("alpha")
        mod1 = sys.modules["plugins.alpha"]
        observed(f"首次 init 后 LOAD_COUNT={mod1.LOAD_COUNT}")
        self.assertEqual(mod1.LOAD_COUNT, 1)

        self.mgr.unload_plugin("alpha")
        observed(f"unload 后：REGISTRY.get('alpha')={REGISTRY.get('alpha')}，"
                 f"'plugins.alpha' in sys.modules = {'plugins.alpha' in sys.modules}")
        self.assertIsNone(REGISTRY.get("alpha"))
        self.assertIn("plugins.alpha", sys.modules)

        self.mgr.load_plugin("alpha")
        mod2 = sys.modules["plugins.alpha"]
        observed(f"重新 load：mod1 is mod2 = {mod1 is mod2}（模块代码未重新执行）")
        self.assertIs(mod1, mod2)

        self.mgr.initialize_plugin("alpha")
        observed(f"再次 init 后 LOAD_COUNT={mod2.LOAD_COUNT}（模块级状态残留，未复位）")
        self.assertEqual(mod2.LOAD_COUNT, 2)

    # E4: 卸载进行中被调用：状态已是 UNLOADING，调用仍被分发
    def test_e4_call_during_unload_is_dispatched(self):
        self.mgr.load_plugin("gamma")
        self.mgr.initialize_plugin("gamma")
        gamma = REGISTRY.get("gamma")

        unloader = threading.Thread(target=self.mgr.unload_plugin, args=("gamma",))
        unloader.start()
        while not gamma.teardown_started:
            pass  # 自旋等 teardown 开始（teardown 内部还会再 sleep 0.3s）
        observed(f"teardown 进行中：state={gamma.state!r}，conn={gamma.conn}")
        self.assertEqual(gamma.state, lifecycle.UNLOADING)

        with self.assertRaises(TypeError) as ctx:
            self.mgr.call("gamma", "work", 1)
        observed(f"UNLOADING 期间 call 仍被分发，踩到已释放资源: {ctx.exception}")
        unloader.join()
        observed(f"卸载完成后：REGISTRY.get('gamma')={REGISTRY.get('gamma')}")
        self.assertIsNone(REGISTRY.get("gamma"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
