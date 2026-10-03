"""核心流程：线索融合 -> 跳级修复 -> 章节切分 -> 可重建区间。

层级融合仲裁规则（冲突取舍依据）：
1. 高置信编号（第X章、a.b.c 多级点分）直接定级，其余线索仅记录冲突；
2. 其余情况加权投票：字号强 2.0 > 编号中 1.5 > 字号弱 1.0 > 位置 0.4；
3. 平票时按 编号 > 字号 > 位置 的来源优先级取舍。

跳级修复策略：
- FILL（补齐）：插入 level 递增 1 的虚拟标题占位，保持编号声明的层级；
- DEMOTE（降级）：把跳级标题降到 上一层级+1，不新增节点。
"""

from __future__ import annotations

import re
from collections import Counter
from typing import List, Optional, Tuple

from .clues import Clue, numbering_clue
from .models import Block, Heading, JumpRecord, Section, SplitResult

FILL = "fill"
DEMOTE = "demote"

_SOURCE_PRIORITY = {"numbering": 0, "size": 1, "position": 2}
_MAX_HEADING_LEN = 60
_TRAILING_PUNCT = re.compile(r"[。！？!?；;，,]$")


def _mode(values, tie_prefer_max: bool):
    """众数；平票时按 tie_prefer_max 取较大/较小值。"""
    counter = Counter(values)
    top = max(counter.values())
    candidates = [value for value, count in counter.items() if count == top]
    chooser = max if tie_prefer_max else min
    return chooser(candidates)


def _heading_like(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped) > _MAX_HEADING_LEN:
        return False
    return not _TRAILING_PUNCT.search(stripped)


def _size_tiers(blocks: List[Block], body_size) -> list:
    """正文字号之上、去重升序的字号档位（tier 0 最大）。"""
    if body_size is None:
        return []
    tiers = sorted({b.size for b in blocks if b.size is not None and b.size > body_size},
                   reverse=True)
    return tiers


def _indent_tiers(blocks: List[Block], body_indent, candidate_indexes) -> list:
    """候选块中、正文缩进之上、去重升序的缩进档位（tier 0 最小）。"""
    if body_indent is None:
        return []
    tiers = sorted({blocks[i].indent for i in candidate_indexes
                    if blocks[i].indent is not None and blocks[i].indent < body_indent})
    return tiers


def _size_vote(block: Block, body_size, size_tiers) -> Optional[Clue]:
    if block.size is None or body_size is None or block.size <= body_size:
        return None
    tier = size_tiers.index(block.size)
    gap = block.size - body_size
    weight = 2.0 if gap >= 2.0 else 1.0
    strength = "强" if weight == 2.0 else "弱"
    return Clue("size", tier + 1, weight,
                f"字号 {block.size}pt 为第 {tier + 1} 大档（正文 {body_size}pt，差 {gap}pt，{strength}）→ L{tier + 1}")


def _position_vote(block: Block, body_indent, indent_tiers) -> Optional[Clue]:
    if block.centered:
        return Clue("position", 1, 0.4, "位置居中 → L1")
    if block.indent is None or body_indent is None or block.indent >= body_indent:
        return None
    if block.indent not in indent_tiers:
        return None
    tier = indent_tiers.index(block.indent)
    return Clue("position", tier + 1, 0.4,
                f"缩进 {block.indent}pt 为第 {tier + 1} 靠左档（正文 {body_indent}pt）→ L{tier + 1}")


def _fuse(clues: List[Clue]) -> Tuple[int, str]:
    """多线索仲裁，返回 (层级, 取舍依据)。"""
    for clue in clues:
        if clue.source == "numbering" and clue.weight >= 3.0:
            conflicts = [c for c in clues if c.level != clue.level]
            reason = f"高置信编号直接定级 L{clue.level}（{clue.detail}）"
            if conflicts:
                others = "；".join(f"{c.source} 线索认为 L{c.level}" for c in conflicts)
                reason += f"；与 {others} 冲突，按『高置信编号 > 字号 > 位置』取编号"
            return clue.level, reason

    scores = {}
    contributors = {}
    for clue in clues:
        scores[clue.level] = scores.get(clue.level, 0.0) + clue.weight
        contributors.setdefault(clue.level, []).append(clue)
    best_score = max(scores.values())
    tied = [level for level, score in scores.items() if score == best_score]
    if len(tied) == 1:
        level = tied[0]
    else:
        # 平票：按来源优先级（编号 > 字号 > 位置）取最优来源所在的层级
        level = min(tied, key=lambda lv: min(_SOURCE_PRIORITY[c.source] for c in contributors[lv]))

    winner = contributors[level][0]
    losers = [c for lv, cs in contributors.items() if lv != level for c in cs]
    reason = f"加权投票取 L{level}（{winner.detail}，得分 {best_score}）"
    if losers:
        others = "；".join(f"{c.source} 线索认为 L{c.level}（权重 {c.weight}）" for c in losers)
        reason += f"；与 {others} 冲突，按权重与『编号 > 字号 > 位置』优先级取舍"
    return level, reason


def _detect_headings(blocks: List[Block]):
    sizes = [b.size for b in blocks if b.size is not None]
    indents = [b.indent for b in blocks if b.indent is not None]
    body_size = _mode(sizes, tie_prefer_max=True) if sizes else None
    body_indent = _mode(indents, tie_prefer_max=False) if indents else None

    # 预筛 + 编号线索（含小数噪声抑制）
    candidates = []
    numbering = {}
    suppressed = []
    for index, block in enumerate(blocks):
        if not _heading_like(block.text):
            continue
        clue, decimal_like = numbering_clue(block.text)
        has_layout = ((block.size is not None and body_size is not None and block.size > body_size)
                      or bool(block.centered)
                      or (block.indent is not None and body_indent is not None
                          and block.indent < body_indent))
        if clue is not None and decimal_like and not has_layout:
            suppressed.append({
                "block_index": index, "text": block.text,
                "reason": "编号疑似小数（a.b 且 b 为个位数），且无字号/位置佐证，按正文处理",
            })
            continue
        if clue is None and not has_layout:
            continue
        candidates.append(index)
        if clue is not None:
            numbering[index] = clue

    size_tiers = _size_tiers(blocks, body_size)
    indent_tiers = _indent_tiers(blocks, body_indent, candidates)

    headings = []
    for index in candidates:
        block = blocks[index]
        clues = []
        if index in numbering:
            clues.append(numbering[index])
        for vote in (_size_vote(block, body_size, size_tiers),
                     _position_vote(block, body_indent, indent_tiers)):
            if vote is not None:
                clues.append(vote)
        if not clues:
            continue
        level, reason = _fuse(clues)
        headings.append(Heading(
            block_index=index, text=block.text, raw_level=level, final_level=level,
            votes={c.source: {"level": c.level, "weight": c.weight, "detail": c.detail}
                   for c in clues},
            reason=reason,
        ))
    return headings, suppressed, body_size, body_indent, size_tiers, indent_tiers


def _repair(headings: List[Heading], strategy: str):
    """跳级检测与修复，返回 (修复后标题列表, 对照记录)。"""
    repaired: List[Heading] = []
    records: List[JumpRecord] = []
    prev_level = 0
    for heading in headings:
        raw = heading.raw_level
        if raw <= prev_level + 1:
            final = raw
            action = "递进一级，保留" if raw == prev_level + 1 else f"同级或回升（L{prev_level}→L{raw}），保留"
        elif strategy == FILL:
            final = raw
            for missing in range(prev_level + 1, raw):
                records.append(JumpRecord(
                    block_index=heading.block_index, text="（虚拟标题占位）",
                    raw_level=missing, prev_level=missing - 1,
                    action=f"补齐：插入虚拟 L{missing} 占位标题", final_level=missing,
                    synthetic=True))
                repaired.append(Heading(
                    block_index=heading.block_index, text="（虚拟标题占位）",
                    raw_level=missing, final_level=missing, synthetic=True,
                    reason="跳级补齐策略插入的虚拟标题，不对应原文块"))
            action = f"跳级 L{prev_level}→L{raw}，补齐 {raw - prev_level - 1} 个虚拟层级后保留 L{raw}"
        else:  # DEMOTE
            final = prev_level + 1
            action = f"跳级 L{prev_level}→L{raw}，降级为 L{final}（上一层级+1）"
        records.append(JumpRecord(
            block_index=heading.block_index, text=heading.text, raw_level=raw,
            prev_level=prev_level, action=action, final_level=final))
        repaired.append(Heading(
            block_index=heading.block_index, text=heading.text, raw_level=raw,
            final_level=final, votes=heading.votes, reason=heading.reason))
        prev_level = final
    return repaired, records


def _build_tree(block_count: int, headings: List[Heading]) -> Section:
    root = Section(heading=None, level=0, start=0, end=block_count)
    stack = [root]
    for heading in headings:
        node = Section(heading=heading, level=heading.final_level, start=heading.block_index, end=block_count)
        while stack[-1].level >= heading.final_level:
            stack.pop()
        stack[-1].children.append(node)
        stack.append(node)
    return root


def _assign_ranges(root: Section, block_count: int) -> None:
    """按前序遍历把 [0, block_count) 划分给各节点（虚拟节点不占块）。"""
    order = []

    def walk(node):
        order.append(node)
        for child in node.children:
            walk(child)

    walk(root)
    anchors = [node.heading.block_index if node.heading else 0 for node in order]
    for pos, node in enumerate(order):
        start = anchors[pos]
        end = block_count
        for nxt in range(pos + 1, len(order)):
            if not order[nxt].synthetic:
                end = anchors[nxt]
                break
        node.start, node.end = start, end


def _intervals(root: Section) -> list:
    result = []

    def walk(node):
        if node.heading is not None and not node.synthetic:
            result.append((node.level, node.heading.block_index, node.start, node.end))
        for child in node.children:
            walk(child)

    walk(root)
    return result


def split_document(blocks: List[Block]) -> SplitResult:
    """主入口：返回切分结果（含两种修复策略的对照数据与章节树）。"""
    headings, suppressed, body_size, body_indent, size_tiers, indent_tiers = _detect_headings(blocks)

    comparisons, trees = {}, {}
    for strategy in (FILL, DEMOTE):
        repaired, records = _repair(headings, strategy)
        root = _build_tree(len(blocks), repaired)
        _assign_ranges(root, len(blocks))
        trees[strategy] = root
        comparisons[strategy] = records

    fill_root = trees[FILL]
    preamble = (0, fill_root.children[0].start) if fill_root.children else (0, len(blocks))
    return SplitResult(
        blocks=blocks, headings=headings, suppressed=suppressed,
        comparisons=comparisons, trees=trees, intervals=_intervals(fill_root),
        preamble=preamble, body_size=body_size, body_indent=body_indent,
        size_tiers=size_tiers, indent_tiers=indent_tiers,
    )


def rebuild_source(result: SplitResult, strategy: str = FILL) -> str:
    """从切分结果重建规范文本。

    各真实节点的块区间是 [0, N) 的一个划分（前置内容挂在根节点），
    虚拟节点不对应任何块；按区间顺序拼接即可逐字符还原。
    """
    from .source import dump_source

    root = result.trees[strategy]
    intervals = []

    def walk(node):
        if node.heading is not None and not node.synthetic:
            intervals.append((node.start, node.end))
        for child in node.children:
            walk(child)

    walk(root)
    intervals.sort()
    pieces, cursor = [], 0
    for start, end in intervals:
        if start > cursor:
            pieces.extend(result.blocks[cursor:start])
        pieces.extend(result.blocks[start:end])
        cursor = end
    pieces.extend(result.blocks[cursor:])
    return dump_source(pieces)
