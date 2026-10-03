"""punctnorm：标点规范化（全/半角上下文转换）与成对符号配对库。

典型用法::

    from punctnorm import normalize

    r = normalize("他说：（你好[world)")
    print(r.text)          # 修复后的文本
    for issue in r.issues: # 配对冲突报告
        print(issue)
"""

from .convert import Change, diff_width, normalize_width
from .normalize import NormalizeResult, normalize
from .pairs import Issue, PairResult, pair

__all__ = [
    "normalize", "NormalizeResult",
    "pair", "PairResult", "Issue",
    "normalize_width", "diff_width", "Change",
]
