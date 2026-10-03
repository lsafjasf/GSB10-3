"""Suffix Automaton (SAM), standard library only.

Linear-size (states <= 2n-1 for n >= 2) and expected-linear-time construction.
Supports: membership, occurrence count, number of distinct substrings,
longest common substring against another string.
"""

from collections import Counter


class _State:
    __slots__ = ("length", "link", "next", "occ")

    def __init__(self, length, link):
        self.length = length
        self.link = link
        self.next = {}
        self.occ = 0


class SuffixAutomaton:
    """SAM built over a single string.

    Occurrence counts are materialized lazily on first query that needs them;
    distinct-substring counting and traversal do not require propagation.
    """

    def __init__(self, text=""):
        self.root = _State(0, -1)
        self.states = [self.root]
        self.last = 0
        self.text = ""
        self._propagated = True
        for ch in text:
            self.extend(ch)
        self.text = text

    @classmethod
    def from_string(cls, text):
        return cls(text)

    def extend(self, ch):
        """Append one character; expected O(1) amortized, O(n) for n chars."""
        states = self.states
        cur = len(states)
        states.append(_State(states[self.last].length + 1, 0))
        states[cur].occ = 1
        p = self.last
        while p != -1 and ch not in states[p].next:
            states[p].next[ch] = cur
            p = states[p].link
        if p == -1:
            states[cur].link = 0
        else:
            q = states[p].next[ch]
            if states[p].length + 1 == states[q].length:
                states[cur].link = q
            else:
                clone = len(states)
                states.append(
                    _State(states[p].length + 1, states[q].link)
                )
                states[clone].next = dict(states[q].next)
                states[clone].occ = 0
                while p != -1 and states[p].next.get(ch) == q:
                    states[p].next[ch] = clone
                    p = states[p].link
                states[q].link = clone
                states[cur].link = clone
        self.last = cur
        self._propagated = False

    def _order_by_length(self):
        """States sorted by max length (counting sort), O(number of states)."""
        max_len = max((s.length for s in self.states), default=0)
        count = [0] * (max_len + 1)
        for s in self.states:
            count[s.length] += 1
        for i in range(1, max_len + 1):
            count[i] += count[i - 1]
        order = [0] * len(self.states)
        for i in range(len(self.states) - 1, -1, -1):
            length = self.states[i].length
            count[length] -= 1
            order[count[length]] = i
        return order

    def _propagate(self):
        if self._propagated:
            return
        for v in reversed(self._order_by_length()):
            link = self.states[v].link
            if link != -1:
                self.states[link].occ += self.states[v].occ
        self._propagated = True

    def _walk(self, pattern):
        """Return state id after matching pattern, or -1 if it is absent."""
        v = 0
        for ch in pattern:
            nxt = self.states[v].next.get(ch)
            if nxt is None:
                return -1
            v = nxt
        return v

    def contains(self, pattern):
        """True iff `pattern` (non-empty) is a substring; "" returns False."""
        if not pattern:
            return False
        return self._walk(pattern) != -1

    def count_occurrences(self, pattern):
        """Number of (possibly overlapping) occurrences; 0 if absent/empty."""
        if not pattern:
            return 0
        self._propagate()
        v = self._walk(pattern)
        return 0 if v == -1 else self.states[v].occ

    def count_distinct_substrings(self, include_empty=False):
        """Number of distinct non-empty substrings of the built text.

        Each state v contributes (len[v] - len[link[v]]) new substrings.
        """
        total = 0
        for v in self.states[1:]:
            total += v.length - self.states[v.link].length
        return total + (1 if include_empty else 0)

    def max_occurrence(self):
        """Maximum occurrence count among non-empty substrings."""
        self._propagate()
        if len(self.states) == 1:
            return 0
        return max(s.occ for s in self.states[1:])

    def longest_common_substring(self, other):
        """LCS of the built text and `other`.

        Runs the other string through this SAM: O(len(other)).
        Returns (length, substring); ("" , 0-length) when none.
        """
        v = 0
        length = 0
        best_len = 0
        best_end = 0
        for i, ch in enumerate(other):
            while v != 0 and ch not in self.states[v].next:
                v = self.states[v].link
                length = self.states[v].length
            if ch in self.states[v].next:
                v = self.states[v].next[ch]
                length += 1
            else:
                length = 0
            if length > best_len:
                best_len = length
                best_end = i + 1
        return best_len, other[best_end - best_len:best_end]

    @property
    def state_count(self):
        return len(self.states)

    def transition_count(self):
        return sum(len(s.next) for s in self.states)

    def occurrence_table(self):
        """Counter of every distinct substring (O(number of substrings)).

        Intended for short strings / cross-checking only.
        """
        self._propagate()
        table = Counter()
        stack = [(0, "")]
        while stack:
            v, prefix = stack.pop()
            for ch, to in self.states[v].next.items():
                piece = prefix + ch
                table[piece] = self.states[to].occ
                stack.append((to, piece))
        return table
