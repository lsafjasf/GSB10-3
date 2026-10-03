# -*- coding: utf-8 -*-
"""模糊匹配与译文复用库（仅依赖 Python 标准库）。

设计要点
--------
1. 相似度同时考虑三件事：
   - 词序：对词干归一化后的词序列做 difflib 序列比对（SequenceMatcher），
     词序颠倒会显著掉分；
   - 变格/词形：对英文做轻量词干归一（users->user, uploaded->upload,
     running->run ...），中文按字（bigram）处理，避免同一意思因形态不同失分；
   - 标点：标点差异单独少量计分（10%），"仅标点不同" 仍是高分但非满分。

2. 对照算法 ``simple_overlap`` 只看原始词的多重集合 Jaccard 重合度，
   不看词序、不做归一，作为简单重合度基线。

3. 命中结果附带：来源（记忆库/译员/条目 ID）、相似度、差异高亮片段。
   低于阈值的命中 ``reusable=False``，禁止自动复用。
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

__all__ = [
    "DEFAULT_THRESHOLD",
    "HistoryEntry",
    "MatchResult",
    "DiffSegment",
    "normalize",
    "stem",
    "similarity",
    "simple_overlap",
    "diff_segments",
    "render_diff",
    "find_best_match",
]

DEFAULT_THRESHOLD = 0.85

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_WORD_RE = re.compile(r"[0-9a-zA-Z]+(?:'[a-z]+)?")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_VOWELS = set("aeiou")


# ---------------------------------------------------------------- 归一化与分词

def normalize(text: str) -> str:
    """Unicode NFKC（全角->半角）、小写、空白折叠。"""
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def stem(token: str) -> str:
    """轻量英文词干归一，处理常见复数/时态/进行时；中文词原样返回。

    故意做得保守（最短 4 个字符、保留后缀），宁可少归也不要过度归并。
    """
    if not token.isascii() or len(token) < 4 or token.isdigit():
        return token
    for suffix, repl, min_len in (
        ("ies", "y", 5),   # files? 实际走 s；studies -> study
        ("ing", "", 5),    # running -> run，making -> mak(保留足够区分度)
        ("ied", "y", 5),   # studied -> study
        ("ed", "", 4),     # uploaded -> upload
        ("es", "", 4),     # boxes -> box；仅 s/x/z/o/ch/sh 结尾适用
        ("s", "", 4),      # users -> user
    ):
        if token.endswith(suffix) and len(token) >= min_len:
            base = token[: -len(suffix)] + repl
            if suffix == "es" and not (
                base[-1:] in ("s", "x", "z", "o")
                or base.endswith(("ch", "sh"))
            ):
                continue  # files -> file 应走 "s" 规则
            # running / stopped：去掉双写尾辅音
            if suffix in ("ing", "ed") and len(base) >= 2 and base[-1] == base[-2] \
                    and base[-1] not in _VOWELS:
                base = base[:-1]
            return base
    return token


def _raw_tokens(text: str) -> List[tuple]:
    """返回有序的 (kind, token)，kind 为 'word' 或 'punct'。"""
    text = normalize(text)
    tokens: List[tuple] = []
    if _CJK_RE.search(text):
        for ch in text:
            if ch.isspace():
                continue
            tokens.append(("punct", ch) if _PUNCT_RE.fullmatch(ch) else ("word", ch))
    else:
        for m in re.finditer(r"[0-9a-zA-Z]+(?:'[a-z]+)?|[^\w\s]", text):
            tok = m.group(0)
            tokens.append(("punct", tok) if _PUNCT_RE.fullmatch(tok) else ("word", tok))
    return tokens


def content_tokens(text: str, do_stem: bool = True) -> List[str]:
    """实词词元序列（去掉标点，英文可做词干归一）。中文为单字。"""
    toks = [t for kind, t in _raw_tokens(text) if kind == "word"]
    if do_stem:
        toks = [stem(t) if t.isascii() else t for t in toks]
    return toks


def punct_tokens(text: str) -> List[str]:
    return [t for kind, t in _raw_tokens(text) if kind == "punct"]


def display_tokens(text: str) -> List[str]:
    """保留标点、保留原形，用于差异高亮展示。"""
    return [t for _, t in _raw_tokens(text)]


def _display_pairs(text: str) -> List[tuple]:
    """(归一化词元, 原文词元) 对：匹配用前者，展示用后者（保留大小写）。"""
    original = unicodedata.normalize("NFKC", text or "")
    original = re.sub(r"\\s+", " ", original).strip()
    if _CJK_RE.search(original):
        pairs = []
        for ch in original:
            if ch.isspace():
                continue
            pairs.append((ch.lower(), ch))
        return pairs
    pairs = []
    for m in re.finditer(r"[0-9a-zA-Z]+(?:'[a-z]+)?|[^\w\s]", original):
        tok = m.group(0)
        pairs.append((tok.lower(), tok))
    return pairs


def _join(tokens: Sequence[str]) -> str:
    """词之间加空格；中文字符之间、标点前不加。"""
    out = ""
    for tok in tokens:
        if not out:
            out = tok
        elif _CJK_RE.match(tok) or _CJK_RE.match(out[-1]) or _PUNCT_RE.fullmatch(tok):
            out += tok
        else:
            out += " " + tok
    return out


# ---------------------------------------------------------------- 相似度

def _multiset_jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    ca, cb = Counter(a), Counter(b)
    union = sum((ca | cb).values())
    if union == 0:
        return 1.0
    return sum((ca & cb).values()) / union


def simple_overlap(text_a: str, text_b: str) -> float:
    """基线：原始词（不归一、不看顺序、丢弃标点）的多重集合重合度。"""
    a = content_tokens(text_a, do_stem=False)
    b = content_tokens(text_b, do_stem=False)
    return round(_multiset_jaccard(a, b), 4)


def similarity(text_a: str, text_b: str, order_weight: float = 0.7) -> float:
    """词序 + 变格 + 标点的综合相似度，取值 [0, 1]。"""
    ca = content_tokens(text_a)
    cb = content_tokens(text_b)
    pa = punct_tokens(text_a)
    pb = punct_tokens(text_b)

    if not ca and not cb:
        # 都没有实词：仅标点/空白，归一化后相等视为满分
        return 1.0 if normalize(text_a) == normalize(text_b) else round(
            difflib.SequenceMatcher(None, pa, pb).ratio(), 4)

    order_score = difflib.SequenceMatcher(None, ca, cb).ratio()
    overlap_score = _multiset_jaccard(ca, cb)
    content = order_weight * order_score + (1.0 - order_weight) * overlap_score

    punct_score = 1.0 if not pa and not pb else difflib.SequenceMatcher(
        None, pa, pb).ratio()
    return round(0.9 * content + 0.1 * punct_score, 4)


# ---------------------------------------------------------------- 差异高亮

@dataclass
class DiffSegment:
    kind: str          # equal / delete / insert / replace
    old: str = ""      # 历史句侧
    new: str = ""      # 待译句侧


def diff_segments(history_text: str, query_text: str) -> List[DiffSegment]:
    """以历史句为 old、待译句为 new，产出 token 级差异段。"""
    old_pairs = _display_pairs(history_text)
    new_pairs = _display_pairs(query_text)
    matcher = difflib.SequenceMatcher(
        None, [p[0] for p in old_pairs], [p[0] for p in new_pairs])
    result: List[DiffSegment] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        result.append(DiffSegment(
            tag,
            _join([p[1] for p in old_pairs[i1:i2]]),
            _join([p[1] for p in new_pairs[j1:j2]]),
        ))
    return [s for s in result if s.kind != "equal" or s.old]


def render_diff(segments: Sequence[DiffSegment]) -> str:
    """高亮格式：相同部分原样；删除 [-x-]；新增 {+y+}；替换 [-x-]{+y+}。"""
    parts: List[str] = []
    for seg in segments:
        if seg.kind == "equal":
            parts.append(seg.old)
        elif seg.kind == "delete":
            parts.append(f"[-{seg.old}-]")
        elif seg.kind == "insert":
            parts.append(f"{{+{seg.new}+}}")
        else:
            parts.append(f"[-{seg.old}-]{{+{seg.new}+}}")
    return " ".join(p for p in parts if p)


# ---------------------------------------------------------------- 历史句库匹配

@dataclass
class HistoryEntry:
    source: str          # 历史原文
    translation: str     # 历史译文
    origin: str = ""     # 来源：记忆库/项目/译员
    entry_id: str = ""   # 条目 ID


@dataclass
class MatchResult:
    query: str
    score: float
    threshold: float
    entry: Optional[HistoryEntry]
    reusable: bool
    diff: str

    def describe(self) -> str:
        if self.entry is None:
            return "历史句库为空，无命中。"
        flag = "允许自动复用" if self.reusable else "低于阈值，仅建议人工确认"
        return (
            f"[{flag}] 相似度={self.score:.2f}（阈值 {self.threshold:.2f}）\n"
            f"  来源: {self.entry.origin} | 条目: {self.entry.entry_id}\n"
            f"  历史句: {self.entry.source}\n"
            f"  复用译文: {self.entry.translation}\n"
            f"  差异高亮: {self.diff}"
        )


def find_best_match(
    query: str,
    history: Sequence[HistoryEntry],
    threshold: float = DEFAULT_THRESHOLD,
    scorer: Callable[[str, str], float] = similarity,
) -> MatchResult:
    """在历史句库中找最高分命中；低于阈值时 reusable=False。"""
    best: Optional[HistoryEntry] = None
    best_score = -1.0
    for entry in history:
        score = scorer(query, entry.source)
        if score > best_score:
            best, best_score = entry, float(score)

    if best is None:
        return MatchResult(query, 0.0, threshold, None, False, "")

    reusable = best_score >= threshold
    diff = render_diff(diff_segments(best.source, query))
    return MatchResult(query, round(best_score, 4), threshold, best, reusable, diff)
