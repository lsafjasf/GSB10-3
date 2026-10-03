"""
regex_automata.py — 正则规则 -> 自动机 -> 等价判定（仅标准库）

支持的语法：
  连接（隐式）、并 `|`、闭包 `*`（以及语法糖 `+`、`?`）、括号
  字符类 `[abc]`、`[a-z]`、取反 `[^...]`（在指定字母表上展开）
  空语言 `∅`（ASCII 别名 `@`）、空串 `ε`（ASCII 别名 `%`）
  反斜杠转义任意特殊字符

等价判定流程（不依赖任何采样）：
  正则 -> AST -> Thompson NFA -> 子集构造 DFA（完全化）
  -> Hopcroft 最小化 -> 规范化编号（canonical form）
  两条规则等价 当且仅当 二者的 canonical form 完全相等。

包含关系 L(A) ⊆ L(B) 用乘积自动机判空：A × ¬B 中不存在
可达的 (终态, 非终态) 状态对。
"""

from __future__ import annotations

import string
from collections import deque
from dataclasses import dataclass
from typing import Dict, FrozenSet, Optional, Set, Tuple

DEFAULT_NEG_CLASS_ALPHABET = frozenset(string.ascii_lowercase + string.digits)

EPSILON = "ε"
EMPTY_LANG = "∅"


# ---------------------------------------------------------------- AST

@dataclass(frozen=True)
class Empty:            # 空语言 ∅
    pass

@dataclass(frozen=True)
class Eps:              # 空串 ε
    pass

@dataclass(frozen=True)
class Chars:            # 单字符集合（字符类 / 字面量）
    chars: FrozenSet[str]

@dataclass(frozen=True)
class Cat:
    parts: Tuple

@dataclass(frozen=True)
class Alt:
    options: Tuple

@dataclass(frozen=True)
class Star:
    inner: object


# ---------------------------------------------------------------- 解析

_SPECIAL = set("|*+?()[]\\")

class ParseError(ValueError):
    pass


def parse(pattern: str, neg_alphabet: FrozenSet[str] = DEFAULT_NEG_CLASS_ALPHABET):
    """把正则文本解析成 AST，并做代数化简（∅/ε 传播）。"""
    tokens = list(pattern)
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def next_ch():
        nonlocal pos
        ch = tokens[pos]
        pos += 1
        return ch

    def parse_alt():
        options = [parse_cat()]
        while peek() == "|":
            next_ch()
            options.append(parse_cat())
        return make_alt(options)

    def parse_cat():
        parts = []
        while peek() is not None and peek() not in "|)":
            parts.append(parse_rep())
        return make_cat(parts)

    def parse_rep():
        atom = parse_atom()
        while peek() in ("*", "+", "?"):
            op = next_ch()
            if op == "*":
                atom = make_star(atom)
            elif op == "+":
                atom = make_cat([atom, make_star(atom)])
            else:  # ?
                atom = make_alt([atom, Eps()])
        return atom

    def parse_atom():
        ch = peek()
        if ch is None:
            raise ParseError("意外的表达式结尾")
        if ch == "(":
            next_ch()
            node = parse_alt()
            if peek() != ")":
                raise ParseError("缺少右括号")
            next_ch()
            return node
        if ch == "[":
            return parse_class()
        if ch in (EMPTY_LANG, "@"):
            next_ch()
            return Empty()
        if ch in (EPSILON, "%"):
            next_ch()
            return Eps()
        if ch == "\\":
            next_ch()
            esc = next_ch() if peek() is not None else None
            if esc is None:
                raise ParseError("转义符后缺少字符")
            return Chars(frozenset({esc}))
        if ch in _SPECIAL:
            raise ParseError(f"非法字符: {ch!r}")
        next_ch()
        return Chars(frozenset({ch}))

    def parse_class():
        next_ch()  # '['
        negated = False
        if peek() == "^":
            negated = True
            next_ch()
        chars: Set[str] = set()
        first = True
        while True:
            ch = peek()
            if ch is None:
                raise ParseError("字符类缺少 ]")
            if ch == "]" and not first:
                next_ch()
                break
            first = False
            if ch == "\\":
                next_ch()
                lo = next_ch()
            else:
                lo = next_ch()
            if peek() == "-" and pos + 1 < len(tokens) and tokens[pos + 1] != "]":
                next_ch()  # '-'
                hi = next_ch()
                if hi == "\\":
                    hi = next_ch()
                if ord(hi) < ord(lo):
                    raise ParseError(f"非法区间 {lo}-{hi}")
                chars.update(chr(c) for c in range(ord(lo), ord(hi) + 1))
            else:
                chars.add(lo)
        if negated:
            chars = set(neg_alphabet) - chars
        if not chars:
            return Empty()
        return Chars(frozenset(chars))

    ast = parse_alt()
    if pos != len(tokens):
        raise ParseError(f"多余的内容: {''.join(tokens[pos:])!r}")
    return ast


# ------------------------------------------------------- AST 代数化简

def make_cat(parts):
    flat = []
    for p in parts:
        if isinstance(p, Empty):          # ∅ 是连接的零元
            return Empty()
        if isinstance(p, Eps):            # ε 是连接的幺元
            continue
        if isinstance(p, Cat):
            flat.extend(p.parts)
        else:
            flat.append(p)
    if not flat:
        return Eps()
    if len(flat) == 1:
        return flat[0]
    return Cat(tuple(flat))


def make_alt(options):
    flat = []
    seen = set()
    for o in options:
        if isinstance(o, Empty):          # ∅ 是并的幺元
            continue
        if isinstance(o, Alt):
            candidates = o.options
        else:
            candidates = (o,)
        for c in candidates:
            if c not in seen:
                seen.add(c)
                flat.append(c)
    if not flat:
        return Empty()
    if len(flat) == 1:
        return flat[0]
    return Alt(tuple(flat))


def make_star(inner):
    if isinstance(inner, (Empty, Eps)):   # ∅* = ε* = ε
        return Eps()
    if isinstance(inner, Star):           # (a*)* = a*
        return inner
    return Star(inner)


def ast_alphabet(ast) -> Set[str]:
    """收集 AST 中出现的全部字符。"""
    out: Set[str] = set()

    def walk(node):
        if isinstance(node, Chars):
            out.update(node.chars)
        elif isinstance(node, Cat):
            for p in node.parts:
                walk(p)
        elif isinstance(node, Alt):
            for o in node.options:
                walk(o)
        elif isinstance(node, Star):
            walk(node.inner)

    walk(ast)
    return out


# ---------------------------------------------------------------- NFA

class NFA:
    def __init__(self):
        self.trans: Dict[Tuple[int, Optional[str]], Set[int]] = {}
        self.start = 0
        self.accept = 1
        self._next = 0

    def new_state(self) -> int:
        self._next += 1
        return self._next

    def add(self, src: int, sym: Optional[str], dst: int):
        self.trans.setdefault((src, sym), set()).add(dst)


def ast_to_nfa(ast) -> NFA:
    """Thompson 构造。∅ -> 无通路；ε -> 一条 ε 边。"""
    nfa = NFA()

    def build(node) -> Tuple[int, int]:
        s, t = nfa.new_state(), nfa.new_state()
        if isinstance(node, Empty):
            pass                                # s 到 t 没有通路
        elif isinstance(node, Eps):
            nfa.add(s, None, t)
        elif isinstance(node, Chars):
            for c in node.chars:
                nfa.add(s, c, t)
        elif isinstance(node, Cat):
            prev = s
            for part in node.parts:
                a, b = build(part)
                nfa.add(prev, None, a)
                prev = b
            nfa.add(prev, None, t)
        elif isinstance(node, Alt):
            for opt in node.options:
                a, b = build(opt)
                nfa.add(s, None, a)
                nfa.add(b, None, t)
        elif isinstance(node, Star):
            a, b = build(node.inner)
            nfa.add(s, None, a)
            nfa.add(s, None, t)
            nfa.add(b, None, a)
            nfa.add(b, None, t)
        else:
            raise TypeError(node)
        return s, t

    nfa.start, nfa.accept = build(ast)
    return nfa


# ---------------------------------------------------------------- DFA

@dataclass(frozen=True)
class DFA:
    """完全确定自动机：trans[s][c] 对所有 s、c 都有定义。"""
    alphabet: Tuple[str, ...]
    start: int
    accepts: FrozenSet[int]
    trans: Tuple[Tuple[int, ...], ...]   # trans[state][char_index] -> state

    def accepts_str(self, word: str) -> bool:
        s = self.start
        for ch in word:
            s = self.trans[s][self.alphabet.index(ch)]
        return s in self.accepts

    def num_states(self) -> int:
        return len(self.trans)


def nfa_to_dfa(nfa: NFA, alphabet: FrozenSet[str]) -> DFA:
    """子集构造，结果完全化（含汇态）。"""
    alpha = tuple(sorted(alphabet))

    def closure(states):
        stack = list(states)
        out = set(states)
        while stack:
            s = stack.pop()
            for d in nfa.trans.get((s, None), ()):
                if d not in out:
                    out.add(d)
                    stack.append(d)
        return frozenset(out)

    def move(states, sym):
        out = set()
        for s in states:
            out.update(nfa.trans.get((s, sym), ()))
        return out

    start_set = closure({nfa.start})
    ids: Dict[frozenset, int] = {start_set: 0}
    work = deque([start_set])
    rows: Dict[int, Tuple[int, ...]] = {}
    accepts: Set[int] = set()

    while work:
        cur = work.popleft()
        cur_id = ids[cur]
        if nfa.accept in cur:
            accepts.add(cur_id)
        row = []
        for sym in alpha:
            nxt = closure(move(cur, sym))
            if nxt not in ids:
                ids[nxt] = len(ids)
                work.append(nxt)
            row.append(ids[nxt])
        rows[cur_id] = tuple(row)

    n = len(ids)
    trans = tuple(rows[i] for i in range(n))
    return DFA(alpha, 0, frozenset(accepts), trans)


# ------------------------------------------------------- 最小化 + 规范化

def minimize(dfa: DFA) -> DFA:
    """Hopcroft 最小化。保证 L(min) == L(dfa)（标准定理）。"""
    n = dfa.num_states()
    alpha_n = len(dfa.alphabet)
    # 反向迁移表
    pred: list[list[list[int]]] = [
        [[] for _ in range(alpha_n)] for _ in range(n)
    ]
    for s in range(n):
        for ci in range(alpha_n):
            pred[dfa.trans[s][ci]][ci].append(s)

    acc = set(dfa.accepts)
    non_acc = set(range(n)) - acc
    P = [b for b in (acc, non_acc) if b]
    block_of = [0] * n
    for i, b in enumerate(P):
        for s in b:
            block_of[s] = i
    W = deque(range(len(P)))

    while W:
        a_idx = W.popleft()
        A = P[a_idx]
        for ci in range(alpha_n):
            X = set()
            for s in A:
                X.update(pred[s][ci])
            if not X:
                continue
            for b_idx in range(len(P)):
                B = P[b_idx]
                inter = B & X
                if not inter:
                    continue
                diff = B - X
                if not diff:
                    continue
                P[b_idx] = diff
                P.append(inter)
                new_idx = len(P) - 1
                for s in inter:
                    block_of[s] = new_idx
                W.append(new_idx)

    # 只保留从初态可达的块
    reachable = set()
    stack = [dfa.start]
    while stack:
        s = stack.pop()
        if s in reachable:
            continue
        reachable.add(s)
        for ci in range(alpha_n):
            stack.append(dfa.trans[s][ci])

    remap: Dict[int, int] = {}
    new_accepts: Set[int] = set()
    new_rows: Dict[int, Tuple[int, ...]] = {}
    for s in sorted(reachable):
        b = block_of[s]
        if b not in remap:
            remap[b] = len(remap)
    for s in reachable:
        b = remap[block_of[s]]
        if s in acc:
            new_accepts.add(b)
        if b not in new_rows:
            new_rows[b] = tuple(
                remap[block_of[dfa.trans[s][ci]]] for ci in range(alpha_n)
            )
    m = len(remap)
    trans = tuple(new_rows[i] for i in range(m))
    return DFA(dfa.alphabet, remap[block_of[dfa.start]],
               frozenset(new_accepts), trans)


def canonical(dfa: DFA):
    """规范化编号：从初态按字母序 BFS 重新编号，得到同构唯一的形式。"""
    order = {dfa.start: 0}
    queue = deque([dfa.start])
    while queue:
        s = queue.popleft()
        for ci in range(len(dfa.alphabet)):
            t = dfa.trans[s][ci]
            if t not in order:
                order[t] = len(order)
                queue.append(t)
    n = len(order)
    trans = [None] * n
    for s, i in order.items():
        trans[i] = tuple(order[dfa.trans[s][ci]]
                         for ci in range(len(dfa.alphabet)))
    accepts = frozenset(order[s] for s in dfa.accepts)
    return (dfa.alphabet, order[dfa.start], accepts, tuple(trans))


# ------------------------------------------------------- 高层 API

def compile_dfa(pattern: str,
                alphabet: Optional[FrozenSet[str]] = None,
                neg_alphabet: FrozenSet[str] = DEFAULT_NEG_CLASS_ALPHABET
                ) -> Tuple[DFA, DFA]:
    """返回 (原始 DFA, 最小化 DFA)。"""
    ast = parse(pattern, neg_alphabet)
    if alphabet is None:
        alphabet = frozenset(ast_alphabet(ast))
    nfa = ast_to_nfa(ast)
    raw = nfa_to_dfa(nfa, alphabet)
    return raw, minimize(raw)


def equivalent(pat1: str, pat2: str,
               alphabet: Optional[FrozenSet[str]] = None) -> bool:
    """判定两条规则是否接受同一语言（基于自动机同一化）。"""
    a1 = parse(pat1)
    a2 = parse(pat2)
    if alphabet is None:
        alphabet = frozenset(ast_alphabet(a1) | ast_alphabet(a2))
    _, m1 = compile_dfa(pat1, alphabet)
    _, m2 = compile_dfa(pat2, alphabet)
    return canonical(m1) == canonical(m2)


def is_subset(dfa_a: DFA, dfa_b: DFA) -> bool:
    """L(dfa_a) ⊆ L(dfa_b)？乘积自动机 A × ¬B 判空（BFS）。"""
    assert dfa_a.alphabet == dfa_b.alphabet
    start = (dfa_a.start, dfa_b.start)
    seen = {start}
    queue = deque([start])
    while queue:
        sa, sb = queue.popleft()
        if sa in dfa_a.accepts and sb not in dfa_b.accepts:
            return False                      # 找到反例串
        for ci in range(len(dfa_a.alphabet)):
            nxt = (dfa_a.trans[sa][ci], dfa_b.trans[sb][ci])
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return True


def enumerate_strings(alphabet, maxlen: int):
    """按长度递增枚举 alphabet* 中长度 ≤ maxlen 的所有串。"""
    alpha = sorted(alphabet)
    yield ""
    cur = [""]
    for _ in range(maxlen):
        nxt = [w + c for w in cur for c in alpha]
        yield from nxt
        cur = nxt
