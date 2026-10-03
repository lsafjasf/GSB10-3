"""中文分词 + 词典热更新（仅标准库）。

切分算法
    基于词频的最短路径（动态规划）：把句子看成位置 0..n 的图，
    词典中每个词是一条边，代价为 -log(freq/total)；未登录字符有
    固定惩罚边。取总代价最小的路径作为切分结果。

词典热更新
    Dictionary 是不可变快照，构建后不再修改。Segmenter.update()
    只做一次引用替换（在锁内完成），原子生效。segment() 在开始
    时抓取一次快照引用，全程使用，绝不中途切换版本，因此正在
    进行的切分结果不会"忽左忽右"。

未登录词回退规则（明确、确定性的）
    1. 连续的 ASCII 字母/数字（含 . _ % + -）合并为一个 token，
       如 "iPhone15"、"v2.0"；
    2. 其余未登录字符逐字成 token；不会整句退化成单字，因为
       动态规划总是优先选择词典中的长词（代价更低）；
    3. 空白字符作为分隔符丢弃，不产生 token；
    4. 标点符号不属于词典时按规则 2 逐字成 token。
"""

import math
import threading

# 未登录单字相对"词频=1 的词"的额外代价（自然对数）
_UNKNOWN_PENALTY = 5.0
# ASCII 串整体作为一个 token 的额外代价（与串长无关，保证整串优先于逐字拆开）
_ASCII_RUN_PENALTY = 2.0

_ASCII_ALNUM = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._%+-"
)


def _is_ascii_alnum(ch):
    return ch in _ASCII_ALNUM


class Dictionary:
    """不可变词典快照。构建后不再修改，可被并发安全读取。"""

    __slots__ = ("freq", "total", "max_len", "version")

    def __init__(self, word_freq, version="v0"):
        self.freq = dict(word_freq)
        self.total = max(sum(self.freq.values()), 1)
        self.max_len = max((len(w) for w in self.freq), default=1)
        self.version = version

    @classmethod
    def from_lines(cls, lines, version="v0"):
        """从 "词语 词频" 行格式构建，# 开头为注释。"""
        word_freq = {}
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            word = parts[0]
            try:
                freq = int(parts[1]) if len(parts) > 1 else 1
            except ValueError:
                freq = 1
            if word:
                word_freq[word] = freq
        return cls(word_freq, version=version)


class Segmenter:
    """分词器。持有当前词典快照的引用，支持原子热更新。"""

    def __init__(self, dictionary):
        if not isinstance(dictionary, Dictionary):
            raise TypeError("expect a Dictionary snapshot")
        self._dict = dictionary
        self._update_lock = threading.Lock()
        # 测试钩子：segment 内部回调，用于在切分中途注入词典更新以验证原子性
        self.test_hook = None

    @property
    def dictionary(self):
        return self._dict

    def update(self, dictionary):
        """原子替换词典快照。

        对正在进行的切分无任何影响（它们持有旧快照的引用）；
        之后发起的切分立即使用新版本。
        """
        if not isinstance(dictionary, Dictionary):
            raise TypeError("expect a Dictionary snapshot")
        with self._update_lock:
            self._dict = dictionary

    def segment(self, text):
        """切分 text，返回 token 列表。全程使用开始时刻的词典快照。"""
        snapshot = self._dict  # 关键：切分开始时刻只抓取一次引用
        if self.test_hook is not None:
            self.test_hook("after_snapshot", self)
        tokens = self._cut(text, snapshot)
        if self.test_hook is not None:
            self.test_hook("before_return", self)
        return tokens

    def _cut(self, text, d):
        n = len(text)
        if n == 0:
            return []
        log_total = math.log(d.total)
        freq = d.freq
        max_len = d.max_len
        unk_cost = log_total + _UNKNOWN_PENALTY
        run_cost = log_total + _ASCII_RUN_PENALTY

        # best[i] = (cost, token_len)：text[i:] 的最小代价与首 token 长度，
        # token_len == 0 表示该位置是空白，零代价跳过。
        best = [(0.0, 0)] * (n + 1)
        for i in range(n - 1, -1, -1):
            ch = text[i]
            if ch.isspace():
                best[i] = (best[i + 1][0], 0)
                continue
            candidates = [(unk_cost, 1)]
            if _is_ascii_alnum(ch):
                j = i + 1
                while j < n and _is_ascii_alnum(text[j]):
                    j += 1
                candidates.append((run_cost, j - i))
            limit = min(max_len, n - i)
            for length in range(1, limit + 1):
                f = freq.get(text[i:i + length])
                if f:
                    candidates.append((log_total - math.log(f), length))
            best_cost, best_len = None, 0
            for cost, length in candidates:
                total = cost + best[i + length][0]
                # 代价相同（浮点误差内）时优先更长的 token，保证结果确定
                if (
                    best_cost is None
                    or total < best_cost - 1e-9
                    or (abs(total - best_cost) <= 1e-9 and length > best_len)
                ):
                    best_cost, best_len = total, length
            best[i] = (best_cost, best_len)

        tokens = []
        i = 0
        while i < n:
            length = best[i][1]
            if length == 0:
                i += 1
                continue
            tokens.append(text[i : i + length])
            i += length
        return tokens
