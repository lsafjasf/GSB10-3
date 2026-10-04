# 实验观察结果

以下输出来自在仓库根目录执行：

```bash
python3 experiments/run_experiments.py -v 2>&1 | tee experiments/observations.txt
```

环境：Python 3 标准库，无第三方依赖；5 个实验全部通过（断言固化的是实际
观察到的行为，包括问题行为）。原始输出同步保存在 `experiments/observations.txt`。

## E0 加载后、初始化前被调用

```
OBSERVED: load 后未 init：REGISTRY 已可见，state='loaded'
OBSERVED: call('alpha','work') 在 init 前被照常分发，靠插件自己的防御检查才抛出: alpha: work() 在 init 之前被调用
```

- 插件在 `load` 之后、`init` 之前就已进入 `REGISTRY`（`sut/pluginfw/loader.py:47`）。
- `call()` 没有任何生命周期守卫（`sut/pluginfw/manager.py:42-47`）。
- 这里抛出的 `RuntimeError` 来自 alpha 自己的检查
  （`sut/plugins/alpha.py:32`），不是框架挡下的。

## E1 重复加载：无去重，旧实例成为幽灵订阅者

```
OBSERVED: 重复 load 返回新实例：first is second = False，注册表条目被覆盖：REGISTRY.get('alpha') is second = True
OBSERVED: 两个实例都收到事件：delivered=2，first.received=[1, 2]，second.received=[2]
OBSERVED: 卸载后事件仍被投递给幽灵实例：delivered=1，first.received=[1, 2, 3]
```

- `load_plugin` 不去重（`sut/pluginfw/manager.py:13-18`），`register`
  直接覆盖（`sut/pluginfw/registry.py:9`）。
- 旧实例已从注册表消失，但它的事件订阅仍挂在 `BUS` 上：两个实例同时收
  事件（`delivered=2`），同一事件被处理两遍。
- `unload_plugin` 只对注册表中当前实例做 `teardown`（`sut/pluginfw/manager.py:37`），
  旧实例的订阅无人退订：卸载后仍有 `delivered=1`，泄漏贯穿之后的整个进程。

## E2 初始化失败：仍留在注册表、仍可调用、无法重试

```
OBSERVED: init 失败后：REGISTRY.get('beta') is None = False，state='failed'
OBSERVED: FAILED 状态下 call 仍被分发，踩到未初始化属性: 'Plugin' object has no attribute 'ready'
OBSERVED: 失败后直接重试 init 被拒绝: beta: failed -> failed
OBSERVED: 瞬时故障后重试：init() 已成功 ready=True，状态却卡在 'failed'，转移被拒绝: delta: failed -> initialized
```

- 失败实例不被摘除（`sut/pluginfw/manager.py:27`），`state='failed'` 但
  `REGISTRY.get('beta')` 非空。
- FAILED 状态下 `call` 照常分发，beta 的 `work()`（`sut/plugins/beta.py:17`）
  访问只有 init 成功才会赋值的 `ready`，抛 `AttributeError`——这正是
  "未初始化就被调用"问题的典型表现。
- beta 永久失败时重试：第二次 init 再抛异常，`failed → failed` 转移不合法
  （转移表 `sut/pluginfw/lifecycle.py:19`）。
- delta 瞬时失败时更矛盾：第二次 `init()` 已成功并置 `ready=True`
  （`sut/plugins/delta.py:17`），但 `failed → initialized` 不在转移表中，
  状态机拒绝承认已完成的初始化。恢复路径只有"重新 load"，而重新 load 又
  命中 E3 的模块缓存问题。

## E3 卸载后再加载：模块对象复用，模块级状态残留

```
OBSERVED: 首次 init 后 LOAD_COUNT=1
OBSERVED: unload 后：REGISTRY.get('alpha')=None，'plugins.alpha' in sys.modules = True
OBSERVED: 重新 load：mod1 is mod2 = True（模块代码未重新执行）
OBSERVED: 再次 init 后 LOAD_COUNT=2（模块级状态残留，未复位）
```

- `unload_plugin` 不清理 `sys.modules`（`sut/pluginfw/manager.py:40`）。
- `load()` 命中缓存分支（`sut/pluginfw/loader.py:37-38`），重新加载得到
  同一个模块对象，模块代码不会重新执行。
- alpha 的模块级计数器（`sut/plugins/alpha.py:8,20`）跨卸载周期保留；
  若插件作者在模块顶层维护连接池/注册表，重载后全部残留；磁盘上修复过的
  插件代码也不会被加载。

## E4 卸载进行中被调用：UNLOADING 不拦截调用

```
OBSERVED: teardown 进行中：state='unloading'，conn=None
OBSERVED: UNLOADING 期间 call 仍被分发，踩到已释放资源: 'NoneType' object does not support item assignment
OBSERVED: 卸载完成后：REGISTRY.get('gamma')={}
```

- 进入卸载时状态先置 `unloading`，但实例在整个 `teardown()` 执行期间都
  留在注册表（`sut/pluginfw/manager.py:36-38`）。
- gamma 的 `teardown` 先释放资源再慢收尾（`sut/plugins/gamma.py:18-19`），
  这期间 `call` 被分发到 `work()`（`sut/plugins/gamma.py:22`），在
  `conn=None` 上索引赋值抛 `TypeError`。
- 若并发来自事件回调，同样的路径也可重入 `unload_plugin`
  （`transition()` 不防重入，`unloading → unloading` 会直接报错，使卸载
  半途而废）。

## 复现命令

```bash
cd <仓库根目录>
python3 experiments/run_experiments.py -v
```
