"""编辑距离（Damerau-Levenshtein，OSA 变体）与近似子串匹配。

仅使用 Python 3 标准库。

支持四类编辑操作，代价均可配置（见 Costs）：
  - insert     插入一个字符
  - delete     删除一个字符
  - substitute 替换一个字符
  - transpose  交换两个相邻字符（OSA：每个子串最多参与一次交换）

距离计算支持阈值提前终止：
  1. 带状剪枝：|i - j| > threshold // min(insert, delete) 的格子不可能是
     代价 <= threshold 的最优路径的一部分，直接跳过；
  2. 行最小值剪枝：某一行所有有效格子的值都超过 threshold 时，最终距离
     必然超过 threshold，立即返回 None。

近似匹配 find_similar() 在长文本中查找与模式串距离不超过阈值（或相似度
不低于阈值）的片段，返回去重叠后的命中位置清单。
"""

from dataclasses import dataclass

INF = float("inf")

__all__ = ["Costs", "distance", "similarity", "find_similar", "Match"]


@dataclass(frozen=True)
class Costs:
    """四类编辑操作的代价配置，默认均为 1（经典 Damerau-Levenshtein）。"""
    insert: int = 1
    delete: int = 1
    substitute: int = 1
    transpose: int = 1

    def __post_init__(self):
        for name in ("insert", "delete", "substitute", "transpose"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cost must be >= 0")


def distance(a, b, costs=Costs(), threshold=None):
    """计算 a -> b 的编辑距离。

    返回 (distance, cells_computed)。
    若给了 threshold 且真实距离 > threshold，返回 (None, cells_computed)，
    此时 cells_computed 小于完整计算的格子数，体现提前终止的收益。
    """
    la, lb = len(a), len(b)
    if threshold is not None and threshold < 0:
        return None, 0

    # 完整 DP（无阈值）也需要保持相同语义
    if threshold is None or threshold == INF:
        band = max(la, lb)
    else:
        step = min(costs.insert, costs.delete)
        # insert/delete 为 0 时无法靠 |i-j| 剪枝，退化为整行计算
        band = threshold // step if step > 0 else lb

    cells = 0
    prev2 = None  # D[i-2][:]
    prev1 = [j * costs.insert for j in range(lb + 1)]  # D[0][:] 或 D[i-1][:]

    if threshold is not None and min(prev1) > threshold:
        return None, lb + 1

    for i in range(1, la + 1):
        lo = max(1, i - band)
        hi = min(lb, i + band)
        row = [INF] * (lb + 1)
        row[0] = i * costs.delete
        ca = a[i - 1]
        row_min = row[0]  # 第 0 列（纯删除前缀）恒为精确值，参与剪枝判断
        for j in range(lo, hi + 1):
            cells += 1
            best = min(
                prev1[j] + costs.delete,                 # 删除 a[i-1]
                row[j - 1] + costs.insert,               # 插入 b[j-1]
                prev1[j - 1] + (0 if ca == b[j - 1] else costs.substitute),
            )
            if (prev2 is not None and i >= 2 and j >= 2
                    and ca == b[j - 2] and a[i - 2] == b[j - 1]):
                t = prev2[j - 2] + costs.transpose
                if t < best:
                    best = t
            row[j] = best
            if best < row_min:
                row_min = best
        if threshold is not None and row_min > threshold:
            return None, cells  # 提前终止：距离必然超过阈值
        prev2, prev1 = prev1, row

    result = prev1[lb]
    if threshold is not None and result > threshold:
        return None, cells
    return result, cells


def similarity(a, b, costs=Costs()):
    """归一化相似度：1 - distance / max(len(a), len(b))；两空串为 1.0。"""
    if not a and not b:
        return 1.0
    d, _ = distance(a, b, costs)
    return 1.0 - d / max(len(a), len(b))


@dataclass(frozen=True)
class Match:
    """一次命中：text[start:end] 与模式串的距离为 distance。"""
    start: int
    end: int
    distance: int

    @property
    def similarity(self):
        span = max(self.end - self.start, 1)
        return 1.0 - self.distance / span


def find_similar(text, pattern, max_distance=None, min_similarity=None,
                 costs=Costs()):
    """在 text 中查找与 pattern 相似的片段，返回去重叠的 Match 清单。

    阈值二选一：
      - max_distance:    编辑距离不超过该值；
      - min_similarity:  相似度 1 - dist/len(pattern) 不低于该值。

    DP 的第一行全为 0，表示匹配可以从文本任意位置开始；D[m][j] 即
    "以 j 结尾的某个文本片段与 pattern 的最小编辑距离"。
    """
    if max_distance is None:
        if min_similarity is None:
            raise ValueError("必须给出 max_distance 或 min_similarity 之一")
        max_distance = int((1.0 - min_similarity) * len(pattern) + 1e-9)

    m, n = len(pattern), len(text)
    if m == 0:
        return [Match(0, 0, 0)]

    # D[i][j] 与对应的片段起点 S[i][j]，各保留两行（交换需要再上一行）
    prev1 = [0] * (n + 1)          # D[0][j] = 0：任意位置免费开局
    prev1_start = list(range(n + 1))
    prev2 = prev2_start = None
    candidates = []

    for i in range(1, m + 1):
        row = [0] * (n + 1)
        row_start = [0] * (n + 1)
        row[0] = i * costs.delete
        row_start[0] = 0
        cp = pattern[i - 1]
        for j in range(1, n + 1):
            # 候选用 (代价, 片段起点) 比较：等代价时保留更长的片段
            best = prev1[j] + costs.delete
            start = prev1_start[j]
            v = row[j - 1] + costs.insert  # 插入 text[j-1]
            s = row_start[j - 1]
            if (v, s) < (best, start):
                best, start = v, s
            v = prev1[j - 1] + (0 if cp == text[j - 1] else costs.substitute)
            s = prev1_start[j - 1]
            if (v, s) < (best, start):
                best, start = v, s
            if (prev2 is not None and i >= 2 and j >= 2
                    and cp == text[j - 2] and pattern[i - 2] == text[j - 1]):
                v = prev2[j - 2] + costs.transpose
                s = prev2_start[j - 2]
                if (v, s) < (best, start):
                    best, start = v, s
            row[j] = best
            row_start[j] = start
        if i == m:
            for j in range(0, n + 1):
                if row[j] <= max_distance:
                    candidates.append(Match(row_start[j], j, row[j]))
        prev2, prev2_start = prev1, prev1_start
        prev1, prev1_start = row, row_start

    return _dedupe(candidates)


def _dedupe(candidates):
    """按距离升序贪心挑选互不重叠的命中，结果按位置升序返回。"""
    chosen = []
    for cand in sorted(candidates, key=lambda x: (x.distance, -(x.end - x.start), x.start)):
        if all(cand.end <= c.start or cand.start >= c.end for c in chosen):
            chosen.append(cand)
    chosen.sort(key=lambda x: x.start)
    return chosen
