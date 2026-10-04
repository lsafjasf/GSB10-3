"""词形还原引擎（纯标准库）。

Lemmatizer：不规则表/同形表/保护表 + 有序后缀规则 + 已知原型词表门控。
NaiveStemmer：刻意“规则加过头”的朴素基线（无例外表、无词表门控、无补 e），
             用于量化“过度归并”（见 selftest 的 metrics 输出）。
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import replace

from . import tables

_EDGE_PUNCT = "'\".,;:!?()[]{}\u2019"


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFKC", s)


def _is_shouting(token: str) -> bool:
    """全大写纯字母词（长度>=2）：API、DOGS、USA。"""
    return len(token) >= 2 and token.isalpha() and token == token.upper()


def _is_mixed_case(token: str) -> bool:
    # iPhone / McDonald 这类内部含大写的专名：保持原样
    return any(ch.isupper() for ch in token[1:])


def _dedouble(stem: str) -> str:
    """去尾重复辅音：runn -> run, stopp -> stop。"""
    if len(stem) >= 2 and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
        return stem[:-1]
    return stem


def _acronym_result(token: str, acronyms, known) -> str | None:
    """全大写词（或其 +s 复数）的处理。

    返回 None 表示“这是全大写的普通词”（DOGS/CATS），调用方应继续走小写规则。
    """
    if token in acronyms:
        return token
    if token.endswith("s") and token[:-1] in acronyms:
        return token[:-1]  # APIs / CDs
    low = token.lower()
    if low in known:
        return None  # DOG -> dogs 是普通词
    if low.endswith("s") and low[:-1] in known:
        return None  # DOGS/CATS：全大写普通词，继续走小写规则
    return token  # 未登记的全大写词按缩写原样保留（USA）


class Lemmatizer:
    def __init__(self,
                 irregular: dict[str, str] | None = None,
                 invariant: frozenset[str] | None = None,
                 protected: frozenset[str] | None = None,
                 acronyms: frozenset[str] | None = None,
                 rules: tuple[tables.SuffixRule, ...] | None = None,
                 known: frozenset[str] | None = None):
        self.irregular = dict(irregular if irregular is not None else tables.IRREGULAR)
        self.invariant = invariant if invariant is not None else tables.INVARIANT
        self.protected = protected if protected is not None else tables.PROTECTED
        self.acronyms = acronyms if acronyms is not None else tables.ACRONYMS
        self.rules = rules if rules is not None else tables.SUFFIX_RULES
        self.known = known if known is not None else tables.KNOWN_LEMMAS

    # ---- 表更新 API --------------------------------------------------------
    def update_irregular(self, mapping: dict[str, str]) -> None:
        self.irregular.update({k.lower(): v for k, v in mapping.items()})

    def update_invariant(self, words) -> None:
        self.invariant = self.invariant | frozenset(words)

    def update_protected(self, words) -> None:
        self.protected = self.protected | frozenset(words)

    def update_acronyms(self, words) -> None:
        self.acronyms = self.acronyms | frozenset(words)

    def update_known(self, words) -> None:
        self.known = self.known | frozenset(words)

    def update_rule(self, name: str, **changes) -> None:
        """替换同名规则；changes 可含 SuffixRule 的任意字段（如 blocked）。"""
        self.rules = tuple(
            replace(rule, **changes) if rule.name == name else rule
            for rule in self.rules)

    def load_overrides(self, path: str) -> None:
        """从 JSON 载入更新（各键均可省略）：
        irregular / invariant / protected / acronyms / known
        """
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if "irregular" in data:
            self.update_irregular(data["irregular"])
        if "invariant" in data:
            self.update_invariant(data["invariant"])
        if "protected" in data:
            self.update_protected(data["protected"])
        if "acronyms" in data:
            self.update_acronyms(data["acronyms"])
        if "known" in data:
            self.update_known(data["known"])

    # ---- 核心还原 ----------------------------------------------------------
    def lemmatize(self, token: str) -> str:
        # 0. NFKC；剥离首尾标点/空白；无拉丁字母则原样返回
        raw = _nfc(token).strip()
        token = raw.strip(_EDGE_PUNCT)
        if not token:
            return "" if not raw else raw
        if not re.search(r"[A-Za-z]", token):
            return token

        word = token.lower()

        # 1. 大写缩写（在小写化之前判定；APIs 这类“缩写+s”也算）
        if _is_shouting(token) or (
                token.endswith("s") and _is_shouting(token[:-1])):
            result = _acronym_result(token, self.acronyms, self.known)
            if result is not None:
                return result
            # 全大写普通词（DOGS）：fall through，按小写走规则
        # 2. 内部含大写的专名（iPhone）-> 原样
        elif _is_mixed_case(token):
            return token

        # 3. 不规则表（最高语言优先级）
        if word in self.irregular:
            return self.irregular[word]
        # 4. 单复同形 / 5. 封闭词保护
        if word in self.invariant or word in self.protected:
            return word

        # 6. 后缀规则（首条命中即停，候选需在已知原型词表内）
        for rule in self.rules:
            if len(word) <= len(rule.suffix) or not word.endswith(rule.suffix):
                continue
            if word in rule.blocked:
                continue
            stem = word[: -len(rule.suffix)]
            if len(stem) < rule.min_stem:
                continue
            if rule.vowel_in_stem and not re.search(r"[aeiou]", stem):
                continue
            for tmpl in rule.templates:
                if tmpl == "{e}":
                    candidate = stem + "e"
                elif tmpl == "{d}":
                    candidate = _dedouble(stem)
                else:
                    candidate = tmpl.format(stem)
                if candidate in self.known:
                    return candidate
        return word

    def lemmatize_text(self, text: str) -> list[str]:
        return [self.lemmatize(tok) for tok in re.findall(r"\S+", text)]

    def build_index(self, docs: dict[str, str]) -> dict[str, list[str]]:
        """构造“归并键 -> 命中文档（去重保序）”的检索倒排，演示实际用途。"""
        index: dict[str, list[str]] = {}
        for doc_id, text in docs.items():
            for key in set(self.lemmatize_text(text)):
                index.setdefault(key, [])
                if doc_id not in index[key]:
                    index[key].append(doc_id)
        return dict(sorted(index.items()))


class NaiveStemmer:
    """朴素基线：只按长度顺序机械剥后缀，无例外表、无词表门控、无补 e。

    代表“规则加过头”的做法，metrics 会同时对它与 Lemmatizer 打分。
    """
    SUFFIXES = (
        "izations", "isations", "ization", "isation",
        "ations", "ation",
        "iness", "iest", "ied", "ies", "ier",
        "sses", "shes", "ches", "xes", "zes",
        "ing", "oes", "est", "ed", "er",
        "ness", "ily", "ly", "es", "s",
    )

    def lemmatize(self, token: str) -> str:
        token = _nfc(token).strip().strip(_EDGE_PUNCT)
        if not token or not re.search(r"[A-Za-z]", token):
            return token
        if _is_shouting(token):
            return token[:-1] if token.endswith("s") else token
        word = token.lower()
        for suf in self.SUFFIXES:
            if len(word) > len(suf) and word.endswith(suf):
                return word[: -len(suf)]
        return word

    def lemmatize_text(self, text: str) -> list[str]:
        return [self.lemmatize(t) for t in re.findall(r"\S+", text)]
