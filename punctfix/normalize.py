"""端到端流水线：宽度规范化 -> 配对分析 -> 修复/标注。

处理顺序说明：先做全/半角规范化，再做配对分析，因此 Issue 中的
pos/line 均针对“宽度规范化之后”的文本（demo 会给出该文本与列尺，
方便对照定位）。
"""

from dataclasses import dataclass, field
from typing import List

from . import pairing, width


@dataclass
class NormalizeResult:
    original: str
    width_normalized: str
    text: str                          # 最终输出（fix 或 mark）
    strategy: str
    changes: List[width.Change] = field(default_factory=list)
    issues: List[pairing.Issue] = field(default_factory=list)
    pairs: List[pairing.Pair] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


def normalize(text: str, strategy: str = "fix",
              straight_single_quotes: bool = False) -> NormalizeResult:
    """规范化文本。

    strategy:
      "fix"  补齐缺失的另一半、按嵌套关系解开交叉、统一配对宽度；
      "mark" 不增删原文，只用 ⟦ ⟧ / ⚠ 标注问题位置；
      "none" 只做宽度规范化与配对分析，不改文本。
    """
    if strategy not in ("fix", "mark", "none"):
        raise ValueError(f"未知策略: {strategy!r}")

    width_text, changes = width.normalize_text(text)
    analyses = pairing.analyze_text(
        width_text, straight_single_quotes=straight_single_quotes)

    out_lines: List[str] = []
    issues: List[pairing.Issue] = []
    pairs: List[pairing.Pair] = []
    for la in analyses:
        issues.extend(la.issues)
        pairs.extend(la.pairs)
        if strategy == "fix":
            out_lines.append(pairing.fix_line(la))
        elif strategy == "mark":
            out_lines.append(pairing.mark_line(la))
        else:
            out_lines.append(la.text)

    return NormalizeResult(
        original=text,
        width_normalized=width_text,
        text="\n".join(out_lines),
        strategy=strategy,
        changes=changes,
        issues=issues,
        pairs=pairs,
    )
