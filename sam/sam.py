"""后缀自动机（Suffix Automaton, SAM）。

仅依赖标准库。构建为严格线性 O(n)：
- 每个字符至多新建一个普通节点，并可能额外克隆一个节点，
  故节点总数 <= 2n（初始节点之外至多 2n-1 个）；
- 转移用 dict，沿后缀链接的总摊还代价为 O(n)；
- 出现次数通过按 maxlen 计数排序后，在 link 树上逆序传播，O(n)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class State:
    length: int = 0                 # 该状态等价类中的最长串长度
    link: int = -1                  # 后缀链接
    transitions: Dict[str, int] = field(default_factory=dict)
    occ: int = 0                    # endpos 集合大小（出现次数）


class SuffixAutomaton:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.states: List[State] = [State()]
        self.last: int = 0
        self.clones: int = 0
        if text:
            self.build(text)

    # ---------------------------------------------------------------- build
    def build(self, text: str) -> None:
        """线性构建。重复调用会重置自动机。"""
        self.text = text
        self.states = [State()]
        self.last = 0
        self.clones = 0

        for ch in text:
            self._extend(ch)

        # 按 maxlen 计数排序（值域 0..n），保证线性。
        n = len(text)
        count = [0] * (n + 1)
        for st in self.states:
            count[st.length] += 1
        for i in range(1, n + 1):
            count[i] += count[i - 1]
        order = [0] * len(self.states)
        for i in range(len(self.states) - 1, -1, -1):
            length = self.states[i].length
            count[length] -= 1
            order[count[length]] = i

        # 在 link 树上自底向上传播 endpos 计数。
        for idx in reversed(order):
            if idx == 0:
                continue
            link = self.states[idx].link
            self.states[link].occ += self.states[idx].occ

        self._order = order

    def _extend(self, ch: str) -> None:
        cur = len(self.states)
        self.states.append(State(length=self.states[self.last].length + 1, occ=1))
        p = self.last
        while p != -1 and ch not in self.states[p].transitions:
            self.states[p].transitions[ch] = cur
            p = self.states[p].link
        if p == -1:
            self.states[cur].link = 0
        else:
            q = self.states[p].transitions[ch]
            if self.states[p].length + 1 == self.states[q].length:
                self.states[cur].link = q
            else:
                clone = len(self.states)
                self.states.append(
                    State(
                        length=self.states[p].length + 1,
                        link=self.states[q].link,
                        transitions=dict(self.states[q].transitions),
                        occ=0,  # 克隆节点不对应新的前缀结束位置
                    )
                )
                self.clones += 1
                while p != -1 and self.states[p].transitions.get(ch) == q:
                    self.states[p].transitions[ch] = clone
                    p = self.states[p].link
                self.states[q].link = clone
                self.states[cur].link = clone
        self.last = cur

    # -------------------------------------------------------------- queries
    def _state_of(self, pattern: str) -> Optional[int]:
        """返回 pattern 结束时所在状态；不存在返回 None。"""
        v = 0
        for ch in pattern:
            nxt = self.states[v].transitions.get(ch)
            if nxt is None:
                return None
            v = nxt
        return v

    def contains(self, pattern: str) -> bool:
        """子串是否出现（空串视为出现）。"""
        if pattern == "":
            return True
        return self._state_of(pattern) is not None

    def count_occurrences(self, pattern: str) -> int:
        """pattern 在文本中的出现次数（允许重叠）；不存在返回 0。"""
        if pattern == "":
            return len(self.text) + 1
        v = self._state_of(pattern)
        return 0 if v is None else self.states[v].occ

    def num_distinct_substrings(self) -> int:
        """不同非空子串数量：对每个状态累加 maxlen[v]-maxlen[link[v]]。"""
        total = 0
        for i in range(1, len(self.states)):
            total += self.states[i].length - self.states[self.states[i].link].length
        return total

    def longest_common_substring(self, other: str) -> str:
        """self.text 与 other 的最长公共子串（等长时取最先匹配到的一个）。"""
        v = 0
        length = 0
        best_len = 0
        best_pos = 0  # best 在 other 中的结束位置（不含）
        for i, ch in enumerate(other, start=1):
            while v != 0 and ch not in self.states[v].transitions:
                v = self.states[v].link
                length = min(length, self.states[v].length)
            if ch in self.states[v].transitions:
                v = self.states[v].transitions[ch]
                length += 1
            else:
                v = 0
                length = 0
            if length > best_len:
                best_len = length
                best_pos = i
        return other[best_pos - best_len:best_pos]

    # ------------------------------------------------------------- metadata
    @property
    def node_count(self) -> int:
        return len(self.states)

    @property
    def length(self) -> int:
        return len(self.text)

    def stats(self) -> Dict[str, int]:
        return {
            "length": len(self.text),
            "nodes": len(self.states),
            "clones": self.clones,
            "transitions": sum(len(s.transitions) for s in self.states),
        }
