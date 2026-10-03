"""成对符号（引号、括号）的嵌套配对检查与修复。

策略说明
--------
1. 配对按栈式嵌套匹配：每个开符号入栈，闭符号与栈顶匹配则出栈。
2. 交叉嵌套（如 ``([)]``）：闭符号在栈中能找到匹配的开符号、但栈顶不是它时，
   判定为交叉冲突，记录冲突双方的位置；修复时先把压在它上面的开符号
   依次补闭，再闭合它自己（即按"后开先闭"补齐）。
3. 单边缺失：
   - 多余的闭符号（栈中找不到匹配开符号）：fix 模式删除，mark 模式标注保留。
   - 文本结束时仍未闭合的开符号：fix 模式在文末按逆序补闭，mark 模式在
     文末插入标注占位。
4. 对称引号（``"`` 与 ``'``）没有方向，按"开关切换"处理：栈中已有同类
   未闭合引号时视为闭引号，否则视为开引号。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 开符号 -> 闭符号（全角、半角分别成对，不做跨宽度配对）
PAIRS = {
    "(": ")", "（": "）",
    "[": "]", "［": "］",
    "{": "}", "｛": "｝",
    "【": "】", "《": "》", "〈": "〉",
    "「": "」", "『": "』", "〔": "〕",
    "“": "”", "‘": "’",
}
# 对称引号：开闭同形
SYMMETRIC = {'"', "'"}

OPENERS = set(PAIRS)
CLOSERS = {v: k for k, v in PAIRS.items()}

# 标注模式下使用的可见标记
MARK_OPEN, MARK_CLOSE = "⟦", "⟧"


@dataclass
class Issue:
    kind: str            # 'crossing' | 'unclosed' | 'unmatched_close'
    pos: int             # 触发位置（0 基，原文索引）
    char: str            # 触发字符
    detail: str          # 人类可读说明
    related: list = field(default_factory=list)  # 冲突相关的 (char, pos)

    def __str__(self) -> str:
        return f"[{self.kind}] 第{self.pos + 1}字符 {self.char!r}: {self.detail}"


@dataclass
class PairResult:
    text: str            # 处理后的文本
    issues: list         # Issue 列表
    max_depth: int       # 原文中出现的最大嵌套深度

    @property
    def ok(self) -> bool:
        return not self.issues


def _mark(text: str) -> str:
    return f"{MARK_OPEN}{text}{MARK_CLOSE}"


def pair(text: str, mode: str = "fix") -> PairResult:
    """检查并处理 text 中的成对符号。

    mode: 'fix' 自动补齐/删除；'mark' 保留原文并用 ⟦ ⟧ 标注改动；
          'report' 只报告不改动。
    """
    if mode not in ("fix", "mark", "report"):
        raise ValueError(f"未知模式: {mode}")

    stack = []           # [(开符号, 原文位置)]
    issues: list[Issue] = []
    out: list[str] = []
    max_depth = 0

    def emit_inserted(ch: str) -> None:
        if mode == "mark":
            out.append(_mark("+" + ch))
        elif mode == "fix":
            out.append(ch)
        # report 模式不改动

    for i, ch in enumerate(text):
        if ch in OPENERS or ch in SYMMETRIC:
            # 对称引号：栈里已有同类未闭合 -> 视为闭引号
            if ch in SYMMETRIC and any(c == ch for c, _ in stack):
                _close(i, ch, stack, issues, out, emit_inserted, mode)
            else:
                stack.append((ch, i))
                max_depth = max(max_depth, len(stack))
                out.append(ch)
        elif ch in CLOSERS:
            _close(i, ch, stack, issues, out, emit_inserted, mode)
        else:
            out.append(ch)

    # 文本结束仍未闭合的开符号：逆序补闭
    for opener, pos in reversed(stack):
        closer = PAIRS.get(opener, opener)
        issues.append(Issue(
            "unclosed", pos, opener,
            f"开符号 {opener!r} 未闭合，文末补 {closer!r}",
        ))
        emit_inserted(closer)

    return PairResult("".join(out), issues, max_depth)


def _close(i, ch, stack, issues, out, emit_inserted, mode):
    """处理一个闭符号 ch（位于原文 i）。"""
    want_open = ch if ch in SYMMETRIC else CLOSERS[ch]

    if stack and stack[-1][0] == want_open:
        stack.pop()
        out.append(ch)
        return

    # 在栈的更深处找匹配的开符号 -> 交叉嵌套
    depth = next(
        (d for d in range(len(stack) - 2, -1, -1) if stack[d][0] == want_open),
        None,
    )
    if depth is not None:
        crossed = stack[depth + 1:]
        issues.append(Issue(
            "crossing", i, ch,
            f"闭符号 {ch!r} 与第{stack[depth][1] + 1}字符的开符号 "
            f"{want_open!r} 配对，但与 "
            + "、".join(f"第{p + 1}字符的{c!r}" for c, p in crossed)
            + " 交叉",
            related=[(c, p) for c, p in crossed] + [(want_open, stack[depth][1])],
        ))
        # 修复：先把交叉的开符号补闭，再闭合目标
        for opener, _ in reversed(crossed):
            emit_inserted(PAIRS.get(opener, opener))
        del stack[depth:]
        out.append(ch)
        return

    # 栈中找不到匹配开符号 -> 多余的闭符号
    issues.append(Issue(
        "unmatched_close", i, ch,
        f"闭符号 {ch!r} 没有对应的开符号",
    ))
    if mode == "mark":
        out.append(_mark("-" + ch))
    elif mode == "report":
        out.append(ch)
    # fix 模式：删除
