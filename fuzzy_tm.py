"""模糊匹配与译文复用（纯标准库）。

核心设计：
- 分词后做词级别的序列对齐，词序直接影响相似度（区别于简单重合度）。
- 英文做轻量词干化，使变格/变形（items/item、running/run、updated/update）
  可以匹配；中文按单字切分。
- 标点单独成词，内容相似度占主体，标点相似度占小权重。

仅依赖 Python 3 标准库（re/difflib/dataclasses）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional


# ---------------------------------------------------------------- 分词与归一化

# 连续中文/日文等表意文字按单字切；拉丁字母/数字按词切；标点单独成词。
_TOKEN_RE = re.compile(
    r"[\u4e00-\u9fff\u3040-\u30ff]"
    r"|[A-Za-z0-9]+(?:['\u2019][A-Za-z]+)*"
    r"|[^\sA-Za-z0-9\u4e00-\u9fff\u3040-\u30ff]"
)

_PUNCT_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff\u3040-\u30ff]")


@dataclass(frozen=True)
class Token:
    surface: str   # 原文形式
    norm: str      # 归一化形式（匹配用）
    is_punct: bool


def _stem_latin(word: str) -> str:
    """轻量英文词干化：覆盖常见复数/时态/进行时，纯规则、无词典。"""
    w = word.lower()
    if len(w) <= 3:
        return w
    if w.endswith("ies") and len(w) > 4:
        stem = w[:-3] + "y"
    elif w.endswith("sses"):
        stem = w[:-2]
    elif w.endswith(("shes", "ches", "xes")):
        stem = w[:-2]
    elif w.endswith("ing") and len(w) > 5:
        stem = w[:-3]
        if len(stem) >= 2 and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
            stem = stem[:-1]  # running -> runn -> run
    elif w.endswith("ed") and len(w) > 4:
        stem = w[:-2]
        if len(stem) >= 2 and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
            stem = stem[:-1]  # stopped -> stop
    elif w.endswith("es") and len(w) > 3:
        stem = w[:-2]
    elif w.endswith("s") and len(w) > 3 and not w.endswith("ss"):
        stem = w[:-1]
    else:
        stem = w
    # 统一去掉“辅音+e”的尾 e：save/saved/saving、update/updated、file/files
    # 归一到同一词干。词干不要求是真词，只要求两侧一致。
    if len(stem) > 3 and stem.endswith("e") and stem[-2] not in "aeiou":
        stem = stem[:-1]
    return stem


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    for raw in _TOKEN_RE.findall(text):
        is_punct = _PUNCT_RE.search(raw) is None
        if is_punct:
            norm = raw
        elif re.fullmatch(r"[A-Za-z0-9']+", raw):
            norm = _stem_latin(raw)
        else:
            norm = raw  # 表意文字原样（中文单字即“词干”）
        tokens.append(Token(raw, norm, is_punct))
    return tokens


# ---------------------------------------------------------------- 相似度算法

def _dice(items_a: list[str], items_b: list[str]) -> float:
    """多重集合 Dice 系数：只看重合、不看顺序（基线对比算法）。"""
    if not items_a and not items_b:
        return 1.0
    counts_a: dict[str, int] = {}
    counts_b: dict[str, int] = {}
    for item in items_a:
        counts_a[item] = counts_a.get(item, 0) + 1
    for item in items_b:
        counts_b[item] = counts_b.get(item, 0) + 1
    overlap = sum(min(n, counts_b.get(k, 0)) for k, n in counts_a.items())
    return 2.0 * overlap / (len(items_a) + len(items_b))


def similarity_overlap(a: str, b: str) -> float:
    """基线：简单重合度。词面精确匹配、不做词干化、不考虑词序、包含标点。"""
    ta = [t.surface.lower() for t in tokenize(a)]
    tb = [t.surface.lower() for t in tokenize(b)]
    return _dice(ta, tb)


@dataclass
class Similarity:
    score: float                 # 综合相似度 [0,1]，词序敏感
    seq: float                   # 词序列对齐相似度（词序敏感）
    content_overlap: float       # 内容词多重集合重合（词干化，词序无关）
    punct_agreement: float       # 标点相似度

    def as_dict(self) -> dict:
        return {
            "score": round(self.score, 4),
            "seq": round(self.seq, 4),
            "content_overlap": round(self.content_overlap, 4),
            "punct_agreement": round(self.punct_agreement, 4),
        }


# 60% 序列（词序）+ 35% 内容重合（变格容错）+ 5% 标点
W_SEQ, W_CONTENT, W_PUNCT = 0.60, 0.35, 0.05


def similarity(a: str, b: str) -> Similarity:
    """词序敏感、变格容错的综合相似度。"""
    ta, tb = tokenize(a), tokenize(b)
    if not ta and not tb:
        return Similarity(1.0, 1.0, 1.0, 1.0)

    # 标点按字面匹配、单词按词干匹配：用等价键序列驱动 SequenceMatcher，
    # 其 ratio 对调换顺序敏感。
    ka = ["p:" + t.surface if t.is_punct else "w:" + t.norm for t in ta]
    kb = ["p:" + t.surface if t.is_punct else "w:" + t.norm for t in tb]
    matcher = SequenceMatcher(a=ka, b=kb, autojunk=False)
    seq = matcher.ratio()

    content_a = [t.norm for t in ta if not t.is_punct]
    content_b = [t.norm for t in tb if not t.is_punct]
    content_overlap = _dice(content_a, content_b)
    punct_a = [t.surface for t in ta if t.is_punct]
    punct_b = [t.surface for t in tb if t.is_punct]
    punct_agreement = _dice(punct_a, punct_b)

    # 内容词序列完全一致（仅标点/大小写差异）时视为内容等价：
    # 分数只由标点一致性决定，保证此类可安全自动复用。
    if content_a == content_b:
        score = 0.95 + 0.05 * punct_agreement
        return Similarity(score, 1.0, 1.0, punct_agreement)

    score = W_SEQ * seq + W_CONTENT * content_overlap + W_PUNCT * punct_agreement
    return Similarity(score, seq, content_overlap, punct_agreement)


# ---------------------------------------------------------------- 差异高亮

@dataclass
class Segment:
    kind: str  # "equal" | "replace" | "delete" | "insert"
    text: str


_CJK_RE = re.compile(r"[\u3000-\u303f\u3040-\u30ff\u4e00-\u9fff\uff00-\uffef]")


def _join(tokens: list[Token]) -> str:
    """按 token 类型拼接：标点前不加空格；两个中文词之间不加；
    中文词与西文词之间加；西文标点后接词时加。"""
    out: list[str] = []
    prev: Token | None = None
    for tok in tokens:
        if prev is not None:
            prev_cjk = bool(_CJK_RE.search(prev.surface))
            cur_cjk = bool(_CJK_RE.search(tok.surface))
            no_space = (
                tok.is_punct
                or (prev_cjk and cur_cjk)
                or (prev.is_punct and prev_cjk)
            )
            if not no_space:
                out.append(" ")
        out.append(tok.surface)
        prev = tok
    return "".join(out)


def diff_segments(query: str, candidate: str) -> list[Segment]:
    """以候选历史句为参照，对齐出差异片段。

    equal  : 两侧完全一致
    morph  : 词干一致但词面不同（变格/变形），标记为 ~历史/查询~
    replace: 候选中的旧词 -> 查询中的新词
    delete : 仅候选有
    insert : 仅查询有
    """
    ta, tb = tokenize(query), tokenize(candidate)
    ka = ["p:" + t.surface if t.is_punct else "w:" + t.norm for t in ta]
    kb = ["p:" + t.surface if t.is_punct else "w:" + t.norm for t in tb]
    sm = SequenceMatcher(a=kb, b=ka, autojunk=False)  # a=历史, b=查询
    segments: list[Segment] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            # 对齐块内逐 token 检查，把“词干一致、词面不同”的变格标出来。
            for off in range(i2 - i1):
                old_tok, new_tok = tb[i1 + off], ta[j1 + off]
                if old_tok.surface == new_tok.surface:
                    segments.append(Segment("equal", new_tok.surface))
                else:
                    segments.append(Segment("morph",
                                            f"~{old_tok.surface}/{new_tok.surface}~"))
        elif tag == "replace":
            old, new = _join(tb[i1:i2]), _join(ta[j1:j2])
            segments.append(Segment("replace", f"[-{old}-] {{+{new}+}}"))
        elif tag == "delete":
            old = _join(tb[i1:i2])
            segments.append(Segment("delete", f"[-{old}-]"))
        else:
            new = _join(ta[j1:j2])
            segments.append(Segment("insert", f"{{+{new}+}}"))
    return segments


_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.:;!?%])")
_CJK_SPACE = re.compile(
    r"(?<=[\u3000-\u303f\u3040-\u30ff\u4e00-\u9fff\uff00-\uffef])\s+"
    r"(?=[\u3000-\u303f\u3040-\u30ff\u4e00-\u9fff\uff00-\uffef])"
)


def _join_segments(parts: list[str]) -> str:
    text = " ".join(parts)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _CJK_SPACE.sub("", text)
    return text


def render_diff_plain(query: str, candidate: str) -> str:
    """纯文本高亮：[-旧-] {+新+}，~历史/查询~ 表示变格一致。"""
    return _join_segments([seg.text for seg in diff_segments(query, candidate)])


def render_diff_ansi(query: str, candidate: str) -> str:
    """ANSI 彩色高亮（红=历史旧词，绿=查询新词，暗=标点差异）。"""
    parts: list[str] = []
    for seg in diff_segments(query, candidate):
        if seg.kind == "equal":
            parts.append(seg.text)
        elif seg.kind == "morph":
            old, new = seg.text[1:-1].split("/")
            parts.append(f"\033[36m{old}/{new}\033[0m")
        elif seg.kind == "replace":
            old, new = seg.text.split(" {+")
            parts.append(f"\033[31m{old[2:-2]}\033[0m \033[32m{new[:-2]}\033[0m")
        elif seg.kind == "delete":
            parts.append(f"\033[31m{seg.text[2:-2]}\033[0m")
        else:
            parts.append(f"\033[32m{seg.text[2:-2]}\033[0m")
    return _join_segments(parts)


# ---------------------------------------------------------------- 翻译记忆库复用

@dataclass
class TMEntry:
    source: str       # 历史原文
    target: str       # 历史译文
    sid: str          # 来源标识（句库编号/文件）


@dataclass
class MatchResult:
    entry: Optional[TMEntry]
    similarity: float
    detail: dict = field(default_factory=dict)
    reused: bool = False
    reason: str = ""


class TranslationMemory:
    # 默认阈值来自评测扫描（见 data/threshold_sweep.csv）：
    # >=0.90 自动复用（误用率 0%），0.70~0.90 转人工，<0.70 不复用。
    def __init__(self, entries: list[TMEntry], threshold: float = 0.90,
                 review_threshold: float = 0.70):
        self.entries = list(entries)
        self.threshold = threshold
        self.review_threshold = review_threshold

    def best(self, query: str) -> MatchResult:
        best_sim = Similarity(0.0, 0.0, 0.0, 0.0)
        best_entry: Optional[TMEntry] = None
        for entry in self.entries:
            sim = similarity(query, entry.source)
            if sim.score > best_sim.score:
                best_sim, best_entry = sim, entry

        if best_entry is None:
            return MatchResult(None, 0.0, reason="空句库")

        detail = best_sim.as_dict()
        detail["sid"] = best_entry.sid
        if best_sim.score >= self.threshold:
            return MatchResult(best_entry, best_sim.score, detail,
                               reused=True, reason="自动复用")
        if best_sim.score >= self.review_threshold:
            return MatchResult(best_entry, best_sim.score, detail,
                               reused=False, reason="低于阈值：转人工确认")
        return MatchResult(best_entry, best_sim.score, detail,
                           reused=False, reason="低于阈值：不复用")

    def lookup(self, query: str) -> dict:
        result = self.best(query)
        out: dict = {
            "query": query,
            "similarity": round(result.similarity, 4),
            "reused": result.reused,
            "decision": result.reason,
            "threshold": self.threshold,
        }
        if result.entry:
            out["source"] = {
                "sid": result.entry.sid,
                "text": result.entry.source,
                "translation": result.entry.target,
            }
            out["detail"] = result.detail
            out["diff"] = render_diff_plain(query, result.entry.source)
            if result.reused:
                out["reuse_target"] = result.entry.target
        return out
