# 插件加载器生命周期分析

## 0. 范围与材料说明

本工作区（GSB10-3）初始为空仓库，没有预置的插件框架代码。为产出"结论可指到
具体函数与位置、实验可重复执行"的分析，`sut/` 下提供了一个按任务描述复刻的
参考实现作为被测系统（SUT）：发现、加载、初始化、卸载、调用分发的确散落在
`sut/pluginfw/` 的多个文件中。SUT 已冻结，实验只通过其公开行为观察，不修改
`sut/` 下任何文件。

被测代码结构：

| 文件 | 职责 |
|---|---|
| `sut/pluginfw/lifecycle.py` | 状态常量与转移校验表 |
| `sut/pluginfw/loader.py` | 发现（`discover`）与加载（`load`，import + 实例化 + 登记） |
| `sut/pluginfw/manager.py` | 编排：初始化、卸载、调用分发 |
| `sut/pluginfw/registry.py` | 进程级注册表单例 `REGISTRY` |
| `sut/pluginfw/hooks.py` | 事件总线单例 `BUS` |
| `sut/pluginfw/errors.py` | 异常类型 |
| `sut/plugins/{alpha,beta,gamma,delta}.py` | 示例插件（被测代码的一部分） |

## 1. 状态清单（发现 → 卸载）

状态常量定义于 `sut/pluginfw/lifecycle.py:7-12`。

| 状态 | 含义 | 进入该状态的代码位置 |
|---|---|---|
| `discovered` | 目录扫描到插件文件，仅有描述符，无实例 | `loader.py:16`（`PluginDescriptor.__init__`） |
| `loaded` | 模块已执行、实例已创建并已登记进注册表，**尚未 init** | `loader.py:46`（直接赋值，绕过校验） |
| `initialized` | `plugin.init()` 成功返回 | `manager.py:29`（经 `transition()` 校验） |
| `failed` | `plugin.init()` 抛异常 | `manager.py:27`（经 `transition()` 校验） |
| `unloading` | `unload_plugin` 已开始、`teardown()` 尚未返回 | `manager.py:36`（经 `transition()` 校验） |
| `unloaded` | `teardown()` 返回且已从注册表移除 | `manager.py:39`（经 `transition()` 校验） |

## 2. 转移清单与强制性

"框架强制"指经过 `lifecycle.transition()`（`lifecycle.py:29-36`）按转移表
`_ALLOWED`（`lifecycle.py:15-22`）校验，非法转移抛 `IllegalTransition`。

| # | 转移 | 触发函数 | 框架强制？ | 证据 |
|---|---|---|---|---|
| T1 | (无) → `discovered` | `loader.discover()` | 否（构造描述符即得） | `loader.py:20-27` |
| T2 | `discovered` → `loaded` | `loader.load()` | **否**：`plugin.state = LOADED` 直接赋值，绕过 `transition()` | `loader.py:46` |
| T3 | `loaded` → `initialized` | `PluginManager.initialize_plugin()` | 是 | `manager.py:29` |
| T4 | `loaded` → `failed` | `PluginManager.initialize_plugin()`（init 抛异常时） | 是 | `manager.py:27` |
| T5 | `loaded`/`initialized`/`failed` → `unloading` | `PluginManager.unload_plugin()` | 是 | `manager.py:36`，转移表 `lifecycle.py:17-19` |
| T6 | `unloading` → `unloaded` | `PluginManager.unload_plugin()`（teardown 返回后） | 是 | `manager.py:39` |
| T7 | `unloaded`/`failed` → `loaded`（重新加载） | `loader.load()` | **否**：新实例直接赋值，且 `load()` 不复查旧实例状态 | `loader.py:46` |

关键结构性事实：

- **调用路径完全没有状态检查**。`PluginManager.call()`（`manager.py:42-47`）
  只查 `REGISTRY.get(name)` 是否为 `None`，不读 `plugin.state`。插件在
  `loaded`、`failed`、`unloading` 状态下都可被调用——"已初始化才能被调用"
  这条最重要的约定**没有任何框架级强制**。
- **登记发生在初始化之前**。`loader.load()` 在 `loader.py:47` 把未初始化实例
  写入 `REGISTRY`，初始化是另一个文件里另一个入口（`manager.py:20`）稍后
  才做的事。两个动作之间插件对外可见、可被调用。
- **事实来源不止一处**。注册表（`registry.py:21` 单例）、`sys.modules`
  （`loader.py:37,42`）、事件总线订阅表（`hooks.py:8`）各自持有插件相关
  状态，卸载只清理其中第一个（`manager.py:38`）。

## 3. 不成立的隐含约定

### C1 "load 之后、init 之前，插件不会被调用" —— 不成立

- 依据：`loader.py:47` 未初始化即登记；`manager.py:42-47` `call()` 无状态检查。
- 观察（实验 E0）：`call('alpha','work')` 在 init 前被照常分发；alpha 靠自己
  的防御检查（`plugins/alpha.py:32`）才抛出 `RuntimeError`。不做防御的插件
  （beta，`plugins/beta.py:17`）暴露的是 `AttributeError`（见 C2）。

### C2 "init 失败的插件处于干净状态，不会被调用，可直接重试" —— 不成立

- 依据：失败只把状态置为 `failed`（`manager.py:27`），实例仍留在
  `REGISTRY`；`call()` 不查状态（`manager.py:42-47`）；转移表
  `lifecycle.py:19` 只给 `failed` 留了 `{unloading, loaded}` 两个出口，
  没有 `failed → initialized`。
- 观察（实验 E2）：
  - `beta` init 失败后 `REGISTRY.get('beta')` 非 `None`，`state='failed'`；
  - `call('beta','work')` 仍被分发，抛 `AttributeError: 'Plugin' object has
    no attribute 'ready'`（`plugins/beta.py:17` 访问了只有 init 成功才存在
    的属性）；
  - 直接重试 `initialize_plugin` 抛 `IllegalTransition: beta: failed -> failed`
    （第二次 init 异常后 `manager.py:27` 尝试 `failed → failed`）；
  - 即使是瞬时故障也无法恢复：`delta` 第二次 `init()` 本身成功
    （`plugins/delta.py:17` 已置 `ready=True`），但 `manager.py:29` 的
    `failed → initialized` 转移被拒绝，实例卡在"已初始化资源 + failed 状态"
    的矛盾态。唯一出路是重新 `load`（见 C4 的缓存问题）。

### C3 "重复加载是幂等的 / 会被去重" —— 不成立

- 依据：`manager.py:13-18` `load_plugin` 无去重；`registry.py:9`
  `register` 直接覆盖同名条目；旧实例的事件订阅（`hooks.py:8`）无人清理。
- 观察（实验 E1）：重复 `load_plugin('alpha')` 返回**新实例**并覆盖注册表；
  旧实例成为"幽灵订阅者"——两个实例都收到事件（`delivered=2`）；卸载后
  （只对注册表里的新实例做了 `teardown`）旧实例**仍然**收到事件
  （`delivered=1`，`first.received=[1, 2, 3]`）。订阅永久泄漏。

### C4 "卸载后再加载等价于全新加载" —— 不成立

- 依据：`manager.py:40` 注释处——`unload_plugin` 不清理 `sys.modules`；
  `loader.py:37-38` 同名加载直接命中缓存，模块代码不会重新执行。
- 观察（实验 E3）：卸载后 `'plugins.alpha' in sys.modules` 仍为 `True`；
  重新 load 拿到的 `mod2 is mod1`；模块级计数器 `LOAD_COUNT`
  （`plugins/alpha.py:8,20`）在再次 init 后变成 `2` 而非 `1`——模块级
  全局状态跨"卸载-重载"周期残留。磁盘上修复过的插件代码也不会生效。

### C5 "卸载开始后，调用会被拒绝或阻塞到卸载完成" —— 不成立

- 依据：`manager.py:36-38` 的顺序是：先置 `unloading` → 再执行
  `teardown()` → **然后才** `REGISTRY.remove(name)`。teardown 执行期间
  插件仍在注册表中，而 `call()`（`manager.py:42-47`）不查状态。
- 观察（实验 E4）：`gamma.teardown()`（`plugins/gamma.py:16-19`）先释放
  资源（`conn=None`）再 sleep 收尾；线程在卸载进行中调用
  `call('gamma','work',1)`，调用被照常分发，踩到已释放资源抛
  `TypeError: 'NoneType' object does not support item assignment`
  （`plugins/gamma.py:22`）。

## 4. 实验清单

实验脚本：`experiments/run_experiments.py`（Python 3 标准库，unittest）。

| 实验 | 场景 | 验证的约定 | 关键观察 |
|---|---|---|---|
| E0 | init 前调用 | C1 | 未初始化调用被照常分发 |
| E1 | 重复加载 | C3 | 新实例覆盖注册表；旧实例订阅泄漏，卸载后仍收事件 |
| E2 | 加载（init）失败 | C2 | failed 实例仍可被调用；永久/瞬时失败都无法重试恢复 |
| E3 | 卸载后再加载 | C4 | 同一模块对象，模块级状态残留（`LOAD_COUNT=2`） |
| E4 | 卸载期间被调用 | C5 | `unloading` 期间调用被分发，踩到半释放资源 |

完整运行输出见 `experiments/observations.txt`，逐条解读见
`experiments/OBSERVATIONS.md`。

## 5. 运行方式

```bash
cd <仓库根目录>
python3 experiments/run_experiments.py -v            # 全部 5 个实验
python3 experiments/run_experiments.py -v 2>&1 | tee experiments/observations.txt
```

无第三方依赖，Python 3.8+ 即可。实验只读调用 `sut/` 的公开行为；
`sut/` 为冻结的被测代码，请勿修改（修改后 `ANALYSIS.md` 中的行号证据
与实验断言可能失效）。
