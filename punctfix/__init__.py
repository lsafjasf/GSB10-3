"""punctfix：中文文本标点规范化与成对符号配对（仅标准库）。"""

from .normalize import NormalizeResult, normalize
from .pairing import (Issue, LineAnalysis, Pair, analyze_line, analyze_text,
                      fix_line, mark_line)
from .width import Change, half_to_full_ascii, normalize_line, normalize_text

__version__ = "0.1.0"

__all__ = [
    "normalize", "NormalizeResult",
    "analyze_line", "analyze_text", "fix_line", "mark_line",
    "Issue", "Pair", "LineAnalysis",
    "normalize_line", "normalize_text", "Change", "half_to_full_ascii",
    "__version__",
]
