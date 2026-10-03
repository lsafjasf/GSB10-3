"""规范化流水线：先做全/半角上下文转换，再做成对符号配对。"""

from __future__ import annotations

from dataclasses import dataclass

from .convert import Change, diff_width, normalize_width
from .pairs import Issue, PairResult, pair


@dataclass
class NormalizeResult:
    text: str           # 最终文本
    width_changes: list # List[Change]：全/半角转换对照
    pair_result: PairResult

    @property
    def issues(self) -> list:
        return self.pair_result.issues


def normalize(text: str, pair_mode: str = "fix") -> NormalizeResult:
    """先统一宽度，再配对修复。

    pair_mode: 'fix'（自动补齐/删除）| 'mark'（标注）| 'report'（只报告）。
    """
    changes = diff_width(text)
    widened = normalize_width(text)
    pr = pair(widened, mode=pair_mode)
    return NormalizeResult(pr.text, changes, pr)


__all__ = [
    "normalize", "NormalizeResult",
    "pair", "PairResult", "Issue",
    "normalize_width", "diff_width", "Change",
]
