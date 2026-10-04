# 词形还原与词干提取（纯标准库）

搜索场景：同一词族的不同形态（run / runs / ran / running）归并到同一个键；
同时防止规则过度归并（organ ≠ organization，teacher ≠ teach，news ≠ new）。

只用 Python 3 标准库，无第三方依赖。

## 运行方式

```bash
# 自测 + 断言 + 归并数量报告（两种写法等价，退出码 0 表示全部通过）
python3 -m morpho.selftest
python3 morpho/selftest.py
```

报告会写入仓库根目录 `merge_metrics.json`。

作为库使用：

```python
from morpho import Lemmatizer

lem = Lemmatizer()
lem.lemmatize("running")      # "run"
lem.lemmatize("children")     # "child"
lem.lemmatize("APIs")         # "API"
lem.lemmatize("cafés")        # "café"
lem.lemmatize("teacher")      # "teacher"（不与 teach 归并）
lem.build_index({"d1": "Cats run", "d2": "a cat"})  # {"cat": ["d1","d2"], ...}
```

## 文件

- `tables.py` — 规则表与例外表（`IRREGULAR` / `INVARIANT` / `PROTECTED` /
  `ACRONYMS` / `SUFFIX_RULES` / `KNOWN_LEMMAS` / 规则级 `blocked`）。
- `lemmatizer.py` — `Lemmatizer`（受控还原）与 `NaiveStemmer`（规则加过头的基线）。
- `gold.py` — 黄金数据集：69 个词族、57 个单例陷阱、边界用例、陷阱对。
- `selftest.py` — 词族断言、边界断言、表更新断言、归并数量度量。
- `overrides.example.json` — 表更新的 JSON 示例。

## 优先级（从高到低）

更新任何一张表时，规则的命中顺序不变：

0. **规范化**：NFKC（全角→半角、组合重音→预组合），剥离首尾标点；不含拉丁字母的 token 原样返回。
1. **大写缩写表 `ACRONYMS`**：全大写判定在小写化之前。`APIs/CDs` 去 `s`；
   全大写但小写形态是普通词的（`DOGS/CATS`）继续走普通规则；未登记全大写词（`USA`）按缩写保留。
2. **不规则表 `IRREGULAR`**（surface → lemma）：went→go、children→child、better→good……优先级高于一切后缀规则。
3. **同形不变表 `INVARIANT`**：series/species/sheep/deer/fish 单复同形，规则会误删词尾，直接返回。
4. **封闭词保护表 `PROTECTED`**：this/news/hardly/highly/likely……无论候选是否在词表内都不套规则（显式安全网）。
5. **后缀规则 `SUFFIX_RULES`**：按表顺序匹配，**首条命中即停**（更长/更特异的规则排前面）；
   规则内候选模板按序尝试，产出必须落在**已知原型词表 `KNOWN_LEMMAS`** 内才接受（防过度归并的主闸门）。
   规则自身带：最小词干长度 `min_stem`、元音门控 `vowel_in_stem`、
   规则级例外表 `blocked`（如 -er 的施事名词 teacher/worker/player/runner…）。

## 表如何更新

代码内更新（运行时）：

```python
lem = Lemmatizer()
lem.update_irregular({"zorked": "zork"})  # 新不规则形态
lem.update_known(["zork"])                # 新原型，规则即可命中 zorks/zorking
lem.update_invariant(["wildebeest"])      # 新单复同形
lem.update_protected(["forsooth"])        # 新封闭词
lem.update_acronyms(["FAQ"])              # 新缩写
lem.update_rule("s", blocked=frozenset({"cats"}))  # 规则级例外
```

JSON 更新（部署时）：

```python
lem = Lemmatizer()
lem.load_overrides("morpho/overrides.example.json")
```

自测中包含更新生效的断言（见 `assert_updatable`）。

## 词根一致：词族断言

`run_assertions` 对 `gold.FAMILIES` 中每个词族断言：**该词族的全部形态归并键完全相同，
且键就是词族原型**。例如：

- 规则变化：`organize / organizes / organized / organizing / organization / organizations` → 全部为 `organize`
- 不规则：`go / goes / went / gone / going` → `go`；`child / children` → `child`
- 比较等级：`good / better / best` → `good`
- 非英文字符：`café / cafés` → `café`（NFKC 后判定）
- 缩写：`API / APIs / api` → `API`

另有单例断言（单例不得落到任何词族键）与陷阱对断言（`organ/organization`、
`teacher/teach`、`hardly/hard`、`universe/university` 等必须分开）。

## 过度归并的度量

枚举 277 个词面的全部无序词对（38,226 对），按“黄金是否同词族 × 实际是否同键”分四格：
TP=正确归并、FP=错误归并（过度）、TN=正确分开、FN=错误分开（欠归并）。

| 系统 | 正确归并 TP | 错误归并 FP | 正确分开 TN | 错误分开 FN | 精确率 | 召回率 | 词面命中 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `Lemmatizer`（不规则表+规则+词表门控） | 303 | **0** | 37,923 | **0** | 1.0000 | 1.0000 | 277/277 |
| `NaiveStemmer`（规则加过头的基线） | 97 | **23** | 37,900 | 206 | 0.8083 | 0.3201 | 125/277 |

朴素基线的典型错误归并（FP）：

- 施事后缀：`teacher/worker/player/runner` 被并进 `teach/work/play/run`（running→runn）。
- 副词 -ly：`hardly→hard`、`highly→high`、`likely→like`。
- 派生差异：`organization→organ`、`universes/university→univers`。
- 拼写偶然：`leaves/leaving→leav`。

欠归并（FN=206）主要来自机械剥后缀无法恢复拼写：`running→runn`、`cities→citi`、
`biggest→bigg`，以及完全没有不规则表（went/children/better 等）。
`Lemmatizer` 靠补 e / 去双写 / 不规则表 + 词表门控同时消除 FP 与 FN。

## 覆盖的边界用例（`gold.EDGE_CASES`）

- 空串、纯空白、纯标点、纯数字、含数字（`abc123`、`3D`、`COVID-19`）
- 大写缩写：`IT`（缩写表优先于普通词 it）、`USA`、`APIs/CDs`
- 全大写普通词：`DOGS/CATS` 仍归 `dog/cat`；全角 `ＣＡＴＳ` 经 NFKC 同样命中
- 专名大小写：`iPhone`、`McDonald` 原样返回；缩写 `ＡＰＩ` 经 NFKC 识别
- 非英文字符：`日本語`、`über`、`naïve`、`café`、emoji 原样保留
- 含标点：`don't`、`cats.`、`(dogs)`、`running,`
