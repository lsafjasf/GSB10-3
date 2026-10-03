"""中文分词 + 词典热更新（仅标准库）。

切分算法
--------
基于词典 DAG 的动态规划最短路径：
- 词典词边的代价 = -log10(freq / total)，词频越高代价越低；
- 每个字符另有一条“未登录单字”边，代价 = log10(total) + 2，
  保证任何词典词都优先于未登录回退；
- 取总代价最小的路径即为切分结果。

未登录词回退规则（明确、确定）
------------------------------
1. 未登录字先按单字切出；
2. 输出前把相邻且同字符类别（汉字 / ASCII 字母数字）的未登录单字
   合并为一个词，因此不会整句退化成单字；
3. 标点逐字成词，空白字符跳过。

词典热更新
----------
- Dictionary 构建完成后不可变，可安全地在线程间共享；
- Segmenter.update() 先拿到完整构建好的新词典，再做一次引用赋值，
  原子生效（CPython 中引用赋值是原子操作）；
- Segmenter.segment() 在开始时把词典引用读入局部变量，本次切分全程
  只使用这一个版本，切分中途绝不切换。
"""

from __future__ import annotations

import math

__all__ = ["Dictionary", "Segmenter", "char_class"]


def char_class(ch: str) -> str:
    """字符类别：cjk / alnum / space / punct。"""
    if "一" <= ch <= "鿿":
        return "cjk"
    if ch.isascii() and ch.isalnum():
        return "alnum"
    if ch.isspace():
        return "space"
    return "punct"


_MERGEABLE_CLASSES = ("cjk", "alnum")


class Dictionary:
    """不可变词典：word -> freq。构建完成后不得修改。"""

    __slots__ = ("words", "max_len", "version", "log_total")

    def __init__(self, words, version: str = "?"):
        words = dict(words)
        for word, freq in words.items():
            if not word:
                raise ValueError("空词不入词典")
            if not isinstance(freq, int) or freq <= 0:
                raise ValueError(f"词频必须为正整数: {word!r} -> {freq!r}")
        self.words = words
        self.max_len = max((len(w) for w in words), default=1)
        self.version = version
        self.log_total = math.log10(sum(words.values()) or 1)

    @classmethod
    def from_lines(cls, lines, version: str = "?") -> "Dictionary":
        """每行格式: ``词 词频``（词频可省，默认 1）；# 开头为注释。"""
        words = {}
        for lineno, line in enumerate(lines, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            try:
                freq = int(parts[1]) if len(parts) > 1 else 1
            except ValueError:
                raise ValueError(f"第 {lineno} 行词频非法: {line!r}") from None
            words[parts[0]] = freq
        return cls(words, version=version)

    @classmethod
    def from_file(cls, path: str, version: str | None = None) -> "Dictionary":
        with open(path, encoding="utf-8") as f:
            return cls.from_lines(f, version=version or str(path))

    def word_cost(self, word: str) -> float:
        return self.log_total - math.log10(self.words[word])

    @property
    def unknown_cost(self) -> float:
        # 比任何词典单字都贵（词典单字最低代价为 log_total，freq=1 时）
        return self.log_total + 2.0


def _segment(text: str, d: Dictionary):
    """返回 [(token, is_unknown), ...]。"""
    n = len(text)
    if n == 0:
        return []
    inf = float("inf")
    cost = [inf] * (n + 1)
    cost[0] = 0.0
    back = [None] * (n + 1)  # (prev_index, token_or_None, is_unknown)
    words = d.words
    max_len = d.max_len
    log_total = d.log_total
    unknown_cost = d.unknown_cost
    log10 = math.log10

    for i in range(n):
        base = cost[i]
        if base == inf:
            continue
        ch = text[i]
        if char_class(ch) == "space":
            if base < cost[i + 1]:
                cost[i + 1] = base
                back[i + 1] = (i, None, False)
            continue
        # 词典词边
        upper = i + max_len
        if upper > n:
            upper = n
        for j in range(i + 1, upper + 1):
            freq = words.get(text[i:j])
            if freq is None:
                continue
            c = base + log_total - log10(freq)
            if c < cost[j]:
                cost[j] = c
                back[j] = (i, text[i:j], False)
        # 未登录单字边（标点逐字成词，但不计为未登录词，故不标 unk）
        c = base + unknown_cost
        if c < cost[i + 1]:
            cost[i + 1] = c
            back[i + 1] = (i, ch, char_class(ch) in _MERGEABLE_CLASSES)

    tokens = []
    i = n
    while i > 0:
        prev, tok, unk = back[i]
        if tok is not None:
            tokens.append((tok, unk))
        i = prev
    tokens.reverse()

    # 回退规则第 2 条：相邻同类未登录单字合并
    merged = []
    for tok, unk in tokens:
        if (
            unk
            and merged
            and merged[-1][1]
            and char_class(tok) in _MERGEABLE_CLASSES
            and char_class(merged[-1][0][-1]) == char_class(tok)
        ):
            merged[-1] = (merged[-1][0] + tok, True)
        else:
            merged.append((tok, unk))
    return merged


class Segmenter:
    """分词器。持有当前词典版本，支持原子热更新。"""

    def __init__(self, dictionary: Dictionary):
        self._dict = dictionary

    @property
    def dictionary(self) -> Dictionary:
        return self._dict

    @property
    def version(self) -> str:
        return self._dict.version

    def update(self, dictionary: Dictionary) -> None:
        """原子切换到新词典。调用方应保证新词典已完整构建。"""
        self._dict = dictionary  # 单次引用赋值，读者只见旧版或新版

    def segment_tokens(self, text: str):
        """返回 [(token, is_unknown), ...]；本次调用只用一个词典版本。"""
        d = self._dict  # 快照：之后即使别的线程 update 也不影响本次切分
        return _segment(text, d)

    def segment(self, text: str):
        """返回词列表（空白已跳过，标点逐字成词）。"""
        return [tok for tok, _ in self.segment_tokens(text)]
