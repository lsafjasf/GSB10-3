"""规则表与例外表（均可在运行时更新，见 Lemmatizer.update_* / load_overrides）。

优先级（从高到低），详见 morpho/README.md：
  0. NFKC + 边界标点剥离
  1. 大写缩写表 ACRONYMS（含缩写 + s 复数）
  2. 不规则表 IRREGULAR（surface -> lemma）
  3. 同形不变表 INVARIANT（series/species 等“零派生复数”）
  4. 封闭词保护表 PROTECTED（this/news 等禁止套规则）
  5. 后缀规则 SUFFIX_RULES（按下表顺序，首条命中即停；候选必须在 KNOWN_LEMMAS 中）
     规则级 blocked 集合（如 -er 的施事后缀 teacher）优先于规则自身
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SuffixRule:
    name: str
    suffix: str
    # 候选模板，按顺序尝试：{}=词干, {e}=词干+e, {d}=去重复尾辅音的词干,
    # 其余字面量（如 "{}ss"）拼接在词干上。
    templates: tuple[str, ...]
    min_stem: int = 3
    vowel_in_stem: bool = False
    blocked: frozenset[str] = frozenset()


# ---------------------------------------------------------------------------
# 1) 不规则词表：屈折形态 -> 原型（最高优先级，先于一切后缀规则）
# ---------------------------------------------------------------------------
IRREGULAR: dict[str, str] = {
    # be / 情态助动词
    "am": "be", "is": "be", "are": "be", "was": "be", "were": "be",
    "been": "be", "being": "be",
    "went": "go", "gone": "go",
    "does": "do", "did": "do", "done": "do",
    "has": "have", "had": "have", "having": "have",
    # 不规则动词
    "ran": "run",
    "uses": "use", "used": "use", "using": "use",
    "goes": "go", "going": "go", "doing": "do",
    "api": "API", "apis": "API", "cd": "CD", "cds": "CD",
    "saw": "see", "seen": "see",
    "met": "meet",
    "taught": "teach",
    "thought": "think",
    "brought": "bring",
    "left": "leave",
    # 不规则名词
    "men": "man", "women": "woman", "children": "child",
    "oxen": "ox", "mice": "mouse", "geese": "goose",
    "feet": "foot", "teeth": "tooth", "people": "person",
    "leaves": "leaf", "fishes": "fish",
    "analyses": "analysis",
    # 不规则形容词/副词比较等级
    "better": "good", "best": "good",
    "worse": "bad", "worst": "bad",
    "farther": "far", "further": "far",
    "farthest": "far", "furthest": "far",
    "more": "much", "most": "much", "many": "much",
    # 代词
    "its": "it",
}

# ---------------------------------------------------------------------------
# 2) 同形不变表：单复同形，规则会误删词尾，直接原样返回
# ---------------------------------------------------------------------------
INVARIANT: frozenset[str] = frozenset({
    "series", "species", "sheep", "deer", "fish", "moose",
})

# ---------------------------------------------------------------------------
# 3) 封闭词保护表：即使“去后缀后的候选”在词表中也禁止套规则
#    （安全网；多数词靠 KNOWN_LEMMAS 门控即可挡住，这里显式声明）
# ---------------------------------------------------------------------------
PROTECTED: frozenset[str] = frozenset({
    "this", "his", "news", "lens",
    "physics", "economics", "mathematics", "politics", "ethics",
    "hardly", "highly", "likely",  # -ly 规则的反例
})

# ---------------------------------------------------------------------------
# 4) 大写缩写表：全大写且小写形式不是已知普通词时整体视为缩写；
#    表中列出必须按缩写处理、且小写形式本身也是普通词的项（it -> IT）。
# ---------------------------------------------------------------------------
ACRONYMS: frozenset[str] = frozenset({
    "IT", "API", "CD", "URL", "PDF", "CPU", "GPU", "DNA", "HTTP",
})

# ---------------------------------------------------------------------------
# 5) 规则级例外表：命中这些词面时跳过该条规则（继续尝试后续规则）
# ---------------------------------------------------------------------------
# -er/-est 规则会把施事名词 teacher 错并到 teach；显式封死。
BLOCKED_AGENTIVE: frozenset[str] = frozenset({
    "teacher", "worker", "player", "writer", "singer", "dancer",
    "speaker", "reader", "leader", "farmer", "manager", "builder",
    "printer", "painter", "driver", "runner", "actor", "doctor",
})

# ---------------------------------------------------------------------------
# 后缀规则表：顺序即优先级（更长/更特异的规则在前，首条命中即停）。
# ---------------------------------------------------------------------------
SUFFIX_RULES: tuple[SuffixRule, ...] = (
    # 派生名词（-ization/-ation/-ion），长度最长最先
    SuffixRule("izations", "izations", ("{}ize",), min_stem=3),
    SuffixRule("isations", "isations", ("{}ise",), min_stem=3),
    SuffixRule("ization", "ization", ("{}ize",), min_stem=3),
    SuffixRule("isation", "isation", ("{}ise",), min_stem=3),
    SuffixRule("ations", "ations", ("{}ate", "{}"), min_stem=3),
    SuffixRule("ation", "ation", ("{}ate", "{}"), min_stem=3),
    SuffixRule("ions", "ions", ("{}e", "{}"), min_stem=3),
    SuffixRule("ion", "ion", ("{}e", "{}"), min_stem=3),
    # 名词复数 / 动词第三人称（特异拼写）
    SuffixRule("sses", "sses", ("{}ss",), min_stem=3),
    SuffixRule("shes", "shes", ("{}sh",), min_stem=3),
    SuffixRule("ches", "ches", ("{}ch",), min_stem=3),
    SuffixRule("xes", "xes", ("{}x",), min_stem=3),
    SuffixRule("zes", "zes", ("{}z",), min_stem=3),
    SuffixRule("oes", "oes", ("{}o",), min_stem=3),
    SuffixRule("ies", "ies", ("{}y",), min_stem=2),
    # 形容词比较等级（-y 结尾）
    SuffixRule("iest", "iest", ("{}y",), min_stem=2),
    SuffixRule("ier", "ier", ("{}y",), min_stem=2),
    SuffixRule("iness", "iness", ("{}y",), min_stem=2),
    # 动词 -ing / -ed（先补 e，再裸词干，再去双写辅音）
    SuffixRule("ing", "ing", ("{e}", "{}", "{d}"), min_stem=3),
    SuffixRule("ied", "ied", ("{}y",), min_stem=2),
    SuffixRule("ed", "ed", ("{e}", "{}", "{d}"), min_stem=3),
    # 形容词比较等级
    SuffixRule("er", "er", ("{e}", "{}", "{d}"), min_stem=3,
               vowel_in_stem=True, blocked=BLOCKED_AGENTIVE),
    SuffixRule("est", "est", ("{e}", "{}", "{d}"), min_stem=3,
               vowel_in_stem=True),
    # 名词 -ness / 副词 -ly
    SuffixRule("ness", "ness", ("{}",), min_stem=3),
    SuffixRule("ily", "ily", ("{}y",), min_stem=2),
    SuffixRule("ly", "ly", ("{}",), min_stem=3),
    # 一般 -es（buses, cases, universes ...）与一般 -s
    SuffixRule("es", "es", ("{}e", "{}", "{}s"), min_stem=3),
    SuffixRule("s", "s", ("{}",), min_stem=3),
)

# ---------------------------------------------------------------------------
# 已知原型词表：规则产出的候选必须在此表内才接受（防过度归并的主闸门）。
# 更新新词族时，把原型（lemma）加进这里即可。
# ---------------------------------------------------------------------------
FAMILY_LEMMAS: frozenset[str] = frozenset({
    # 规则覆盖的词族原型（与 morpho/gold.py 的词族键保持一致，自测会校验）
    "cat", "dog", "class", "box", "dish", "wish", "buzz", "bus",
    "case", "city", "study", "play", "like", "use", "run", "stop",
    "plan", "organize", "act", "relate", "generate", "policy",
    "analysis", "universe", "gas", "potato", "work",
    "leaf", "leave", "teach", "meet", "see", "bring", "think",
    "answer", "winter", "fish",
    "be", "go", "do", "have",
    "man", "woman", "child", "ox", "mouse", "goose", "foot", "tooth",
    "person",
    "good", "bad", "far", "much",
    "fast", "big", "large", "happy", "easy", "quick", "hard",
    "early", "high", "dark",
    "it", "her", "our", "your", "their",
    "cafe", "café",
    "api", "cd", "API", "CD",
})

EXTRA_LEMMAS: frozenset[str] = frozenset({
    # 仅作为规则候选闸门使用、不一定在数据集中出现的词根
    "the", "a", "an", "and",
})

KNOWN_LEMMAS: frozenset[str] = FAMILY_LEMMAS | EXTRA_LEMMAS
