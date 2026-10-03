"""编辑距离（插入/删除/替换/相邻交换）与近似子串匹配。仅依赖标准库。

操作代价通过 Costs 配置：
- insert:     插入一个字符的代价
- delete:     删除一个字符的代价
- substitute: 替换一个字符的代价
- transpose:  交换两个相邻字符的代价（Optimal String Alignment 变体，
              即每个子串最多参与一次交换，"ca" -> "abc" 距离为 3 而非 2）

阈值提前终止（edit_distance_bounded）的两条正确性依据：
1. 带状裁剪：从 (0,0) 到 (i,j) 的任意编辑路径，|i-j| 的净变化只能由
   插入/删除贡献，故 d(i,j) >= |i-j| * min(insert, delete)。
   当 |i-j| > threshold / min(insert, delete) 时 d(i,j) > threshold，
   这些单元可直接跳过。
2. 双行最小值剪枝：交换操作一步跨两行，故编辑路径可能跳过某一行，
   但步进最大为 2，不可能连续跳过两行。因此任意路径必然在第 i-1 行
   或第 i 行留下单元，d(m,n) >= min(第 i-1 行最小值, 第 i 行最小值)。
   一旦连续两行的最小值都超过 threshold，即可立即判定“超过阈值”。
"""

from dataclasses import dataclass
from typing import List, NamedTuple, Optional, Sequence

INF = float("inf")


@dataclass(frozen=True)
class Costs:
    """四类编辑操作的代价，均为非负数，默认各为 1。"""

    insert: float = 1.0
    delete: float = 1.0
    substitute: float = 1.0
    transpose: float = 1.0


class BoundedResult(NamedTuple):
    distance: Optional[float]  # 未超阈值时为精确距离，否则为 None
    exceeded: bool             # 是否因超过阈值而提前终止
    cells: int                 # 实际计算的 DP 单元数（用于与完整计算对比）


def edit_distance(a: Sequence, b: Sequence, costs: Costs = Costs()) -> float:
    """完整 O(m*n) 动态规划，返回精确距离。"""
    m, n = len(a), len(b)
    ins, dele, sub, tra = costs.insert, costs.delete, costs.substitute, costs.transpose
    prev2 = None
    prev = [j * ins for j in range(n + 1)]
    for i in range(1, m + 1):
        cur = [i * dele] + [INF] * n
        ai = a[i - 1]
        aim1 = a[i - 2] if i > 1 else None
        for j in range(1, n + 1):
            s = 0.0 if ai == b[j - 1] else sub
            best = prev[j] + dele
            v = cur[j - 1] + ins
            if v < best:
                best = v
            v = prev[j - 1] + s
            if v < best:
                best = v
            if i > 1 and j > 1 and ai == b[j - 2] and aim1 == b[j - 1]:
                v = prev2[j - 2] + tra
                if v < best:
                    best = v
            cur[j] = best
        prev2, prev = prev, cur
    return prev[n]


def edit_distance_bounded(a: Sequence, b: Sequence, threshold: float,
                          costs: Costs = Costs()) -> BoundedResult:
    """带阈值提前终止的距离计算。

    若真实距离 <= threshold，返回精确距离（exceeded=False）；
    否则提前终止，返回 distance=None, exceeded=True。
    """
    m, n = len(a), len(b)
    ins, dele, sub, tra = costs.insert, costs.delete, costs.substitute, costs.transpose
    indel = min(ins, dele)
    band = int(threshold // indel) if indel > 0 else max(m, n)
    cells = 0
    prev2 = None
    prev = [INF] * (n + 1)
    hi0 = min(n, band)
    for j in range(hi0 + 1):
        prev[j] = j * ins
        cells += 1
    prev_row_min = min(prev[:hi0 + 1])
    for i in range(1, m + 1):
        lo = max(0, i - band)
        hi = min(n, i + band)
        cur = [INF] * (n + 1)
        row_min = INF
        ai = a[i - 1]
        aim1 = a[i - 2] if i > 1 else None
        if lo == 0:
            cur[0] = i * dele
            row_min = cur[0]
            cells += 1
        for j in range(max(1, lo), hi + 1):
            s = 0.0 if ai == b[j - 1] else sub
            best = prev[j] + dele
            v = cur[j - 1] + ins
            if v < best:
                best = v
            v = prev[j - 1] + s
            if v < best:
                best = v
            if i > 1 and j > 1 and ai == b[j - 2] and aim1 == b[j - 1]:
                v = prev2[j - 2] + tra
                if v < best:
                    best = v
            cur[j] = best
            if best < row_min:
                row_min = best
            cells += 1
        if prev_row_min > threshold and row_min > threshold:
            return BoundedResult(None, True, cells)
        prev_row_min = row_min
        prev2, prev = prev, cur
    d = prev[n]
    if d > threshold:
        return BoundedResult(None, True, cells)
    return BoundedResult(d, False, cells)


class Match(NamedTuple):
    start: int        # 命中片段起始下标（含）
    end: int          # 命中片段结束下标（不含）
    distance: float   # 片段与模式串的编辑距离
    similarity: float # 归一化相似度，1.0 为完全相同


def _similarity(distance: float, pat_len: int, sub_len: int, unit: float) -> float:
    if unit <= 0:
        return 1.0 if distance == 0 else 0.0
    norm = max(pat_len, sub_len, 1) * unit
    return max(0.0, 1.0 - distance / norm)


def find_similar(text: Sequence, pattern: Sequence,
                 max_distance: Optional[float] = None,
                 min_similarity: Optional[float] = None,
                 costs: Costs = Costs()) -> List[Match]:
    """在长文本中找出与模式串相似的片段，返回命中位置清单。

    阈值二选一：
    - max_distance:    编辑距离不超过该值即命中；
    - min_similarity:  相似度不低于该值即命中（换算为距离阈值）。

    相似度定义为 1 - distance / (max(|pattern|, |片段|) * 单位代价)，
    其中单位代价 = 四类操作代价的最大值。
    时间复杂度 O(|pattern| * |text|)，空间 O(|text|)。
    """
    m, n = len(pattern), len(text)
    if m == 0:
        raise ValueError("pattern 不能为空")
    ins, dele, sub, tra = costs.insert, costs.delete, costs.substitute, costs.transpose
    unit = max(ins, dele, sub, tra)
    if max_distance is None:
        if min_similarity is None:
            raise ValueError("必须给出 max_distance 或 min_similarity 之一")
        max_distance = (1.0 - min_similarity) * m * (unit or 1.0)

    # 第一行全为 0：匹配可以从文本任意位置开始（近似子串匹配的经典技巧）。
    prev = [0.0] * (n + 1)
    pstart = list(range(n + 1))  # 每个单元记录对应片段的起始下标
    prev2 = pstart2 = None
    for i in range(1, m + 1):
        cur = [INF] * (n + 1)
        cstart = [0] * (n + 1)
        cur[0] = i * dele
        pi = pattern[i - 1]
        pim1 = pattern[i - 2] if i > 1 else None
        for j in range(1, n + 1):
            tj = text[j - 1]
            s = 0.0 if pi == tj else sub
            best = prev[j] + dele
            bs = pstart[j]
            v = cur[j - 1] + ins
            if v < best:
                best, bs = v, cstart[j - 1]
            v = prev[j - 1] + s
            if v < best:
                best, bs = v, pstart[j - 1]
            if i > 1 and j > 1 and pi == text[j - 2] and pim1 == tj:
                v = prev2[j - 2] + tra
                if v < best:
                    best, bs = v, pstart2[j - 2]
            cur[j] = best
            cstart[j] = bs
        prev2, pstart2, prev, pstart = prev, pstart, cur, cstart

    raw = [(pstart[j], j, prev[j]) for j in range(1, n + 1) if prev[j] <= max_distance]
    # 同一命中常产生多个相邻终点：按距离升序贪心选取互不重叠的代表。
    raw.sort(key=lambda h: (h[2], h[1] - h[0]))
    chosen = []
    for s, e, d in raw:
        if all(e <= cs or s >= ce for cs, ce, _ in chosen):
            chosen.append((s, e, d))
    chosen.sort(key=lambda h: h[0])
    return [Match(s, e, d, _similarity(d, m, e - s, unit)) for s, e, d in chosen]
