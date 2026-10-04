"""成对符号的嵌套匹配、交叉冲突检测、补齐与标注。

核心模型
--------
* :func:`_scan` 是唯一的匹配事实来源，产出一串有序事件：
  ``pair / cross / unpaired_close / unpaired_open``。分析、修复、标注
  都只消费事件，三者结论保证一致。
* 括号与引号使用互相独立的栈。中文文本中引号与括号交叉（“(…”）不算
  语法错误，因此不跨类别判冲突；同类内部才做嵌套校验。
* 遇到闭符号时，从栈顶向下找同基底开符号：
  - 在栈顶 => 正常配对；开/闭宽度不一致记 ``width_mismatch``；
  - 在更深位置 => ``cross`` 交叉冲突，``blockers`` 列出挡在中间的
    开符号及其位置（这就是“冲突位置”）；
  - 找不到 => ``unpaired_close``。
* 行尾仍在栈中的开符号 => ``unpaired_open``。

修复策略（deterministic）
-------------------------
``fix``：只“补/隔”，不删除、不改写作者意图——
* 交叉点：先把挡路开符号按序闭合，再让当前闭符号闭合它真正的开符号，
  随后把挡路符号按原顺序重新打开（输出重新校验一定平衡）；
* 单边缺失：在另一侧补齐同宽度的“另一半”；
* 宽度不一致的配对：统一成全角形态。

``mark``：原文一字不增删，用 ``⟦ ⟧`` 围住问题符号，交叉点前加 ``⚠``，
供人工决定如何处理。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import symbols as S


@dataclass
class Issue:
    kind: str   # cross / unpaired_close / unpaired_open / width_mismatch
    pos: int    # 0-based，行内位置
    char: str
    line: int = 1                 # 1-based
    end: Optional[int] = None     # exclusive
    expected: Optional[str] = None
    blockers: List[Tuple[int, str]] = field(default_factory=list)
    detail: str = ""

    @property
    def column(self) -> int:
        return self.pos + 1


@dataclass
class Pair:
    open_pos: int
    close_pos: int
    open_char: str
    close_char: str
    family: str       # bracket / quote
    base: str
    width_mismatch: bool = False


@dataclass
class LineAnalysis:
    line_no: int
    text: str
    pairs: List[Pair] = field(default_factory=list)
    issues: List[Issue] = field(default_factory=list)


# ---------------------------------------------------------------- 扫描

def _is_apostrophe(text: str, i: int) -> bool:
    """字母之间的 ASCII ' 视为撇号（it's / O'Reilly），不是引号。"""
    return (
        0 < i < len(text) - 1
        and text[i - 1].isalpha()
        and text[i + 1].isalpha()
    )


def _close_event(events, stack, pos, ch, family, match_pred):
    """通用入栈匹配：栈顶向下找同基底/同字符开符号。"""
    k = None
    for idx in range(len(stack) - 1, -1, -1):
        if match_pred(stack[idx][1]):
            k = idx
            break
    if k is None:
        events.append(("unpaired_close", pos, ch, family, None, None, []))
        return
    match_pos, match_ch = stack[k]
    blockers = list(stack[k + 1:])
    if not blockers:
        events.append(("pair", match_pos, match_ch, pos, ch, family))
    else:
        events.append(("cross", pos, ch, family,
                       match_pos, match_ch, blockers))
    # 匹配者出栈；挡路符号保持打开（修复时等价于“先闭合再重开”）
    match = stack[k]
    del stack[k:]
    stack.extend(blockers)
    return match


def scan_line(text: str, straight_single_quotes: bool = False):
    """扫描一行，返回事件列表。

    事件元组：
      ("pair", open_pos, open_ch, close_pos, close_ch, family)
      ("cross", close_pos, close_ch, family,
               match_pos, match_ch, blockers[(pos, ch), ...])
      ("unpaired_close", pos, ch, family, None, None, [])
      ("unpaired_open", pos, ch, family, None, None, [])
    """
    events: List[tuple] = []
    bstack: List[Tuple[int, str]] = []
    qstack: List[Tuple[int, str]] = []

    for i, ch in enumerate(text):
        if ch in S.OPEN_CHARS:
            bstack.append((i, ch))
        elif ch in S.CLOSE_CHARS:
            base = S.CHAR_BASE[ch]
            _close_event(
                events, bstack, i, ch, "bracket",
                lambda oc, b=base: S.CHAR_BASE[oc] == b)
        elif ch in S.QUOTE_OPEN_CHARS:
            qstack.append((i, ch))
        elif ch in S.QUOTE_CLOSE_CHARS:
            want = S.QUOTE_CLOSE_TO_OPEN[ch]
            _close_event(
                events, qstack, i, ch, "quote",
                lambda oc, w=want: oc == w)
        elif ch == S.STRAIGHT_DOUBLE or (
            straight_single_quotes and ch == S.STRAIGHT_SINGLE
            and not _is_apostrophe(text, i)
        ):
            # 直引号：栈中已有同字符开引号 => 当闭符号；否则当开符号
            if any(oc == ch for _, oc in qstack):
                _close_event(
                    events, qstack, i, ch, "quote",
                    lambda oc, c=ch: oc == c)
            else:
                qstack.append((i, ch))

    for pos, ch in bstack:
        events.append(("unpaired_open", pos, ch, "bracket", None, None, []))
    for pos, ch in qstack:
        events.append(("unpaired_open", pos, ch, "quote", None, None, []))
    return events


def analyze_line(text: str, line_no: int = 1,
                 straight_single_quotes: bool = False) -> LineAnalysis:
    result = LineAnalysis(line_no=line_no, text=text)
    for ev in scan_line(text, straight_single_quotes):
        kind = ev[0]
        if kind == "pair":
            _, op, oc, cp, cc, family = ev
            base = S.CHAR_BASE.get(oc, "straight") if family == "bracket" else "curly"
            wm = (
                family == "bracket"
                and S.is_full_width_bracket(oc) != S.is_full_width_bracket(cc)
            )
            result.pairs.append(Pair(op, cp, oc, cc, family, base, wm))
            if wm:
                result.issues.append(Issue(
                    "width_mismatch", op, oc, line_no, end=cp + 1,
                    detail=f"开/闭符号宽度不一致：{oc} … {cc}"))
        elif kind == "cross":
            _, cp, cc, family, mp, mc, blockers = ev
            innermost = blockers[-1][1]
            expected = (S.OPEN_TO_CLOSE.get(innermost)
                        or S.QUOTE_OPEN_TO_CLOSE.get(innermost, innermost))
            result.issues.append(Issue(
                "cross", cp, cc, line_no, expected=expected,
                blockers=blockers,
                detail=(f"交叉嵌套：列 {cp + 1} 的 {cc} 想闭合列 {mp + 1} 的 {mc}，"
                        f"但 {'、'.join(f'列 {p + 1} 的 {c}' for p, c in blockers)}"
                        f" 尚未闭合（此处按嵌套关系应先出现 {expected}）")))
        elif kind == "unpaired_close":
            _, pos, ch, family, *_ = ev
            label = "闭括号" if family == "bracket" else "闭引号"
            result.issues.append(Issue(
                "unpaired_close", pos, ch, line_no,
                detail=f"多余的{label} {ch}，缺少对应的开符号"))
        else:  # unpaired_open
            _, pos, ch, family, *_ = ev
            label = "开括号" if family == "bracket" else "开引号"
            result.issues.append(Issue(
                "unpaired_open", pos, ch, line_no,
                detail=f"未闭合的{label} {ch}，缺少对应的闭符号"))
    return result


def analyze_text(text: str, straight_single_quotes: bool = False) -> List[LineAnalysis]:
    if not text:
        return [LineAnalysis(1, "")]
    lines = text.split("\n")
    return [
        analyze_line(line, idx + 1, straight_single_quotes)
        for idx, line in enumerate(lines)
    ]


# ---------------------------------------------------------------- 修复

def _mate_open(close_ch: str) -> str:
    return S.CLOSE_TO_OPEN.get(
        close_ch, S.QUOTE_CLOSE_TO_OPEN.get(close_ch, close_ch))


def _mate_close(open_ch: str) -> str:
    return S.OPEN_TO_CLOSE.get(
        open_ch, S.QUOTE_OPEN_TO_CLOSE.get(open_ch, open_ch))


def build_form_map(analysis: LineAnalysis) -> Dict[int, Tuple[str, str]]:
    """宽度不一致的配对统一成全角形态：{open_pos: (new_open, new_close)}。"""
    form_map: Dict[int, Tuple[str, str]] = {}
    for p in analysis.pairs:
        if p.family == "bracket" and p.width_mismatch:
            form_map[p.open_pos] = S.FULL_FORMS[p.base]
    return form_map


def fix_line(analysis: LineAnalysis,
             form_map: Optional[Dict[int, Tuple[str, str]]] = None) -> str:
    """按 fix 策略输出平衡文本（只补不改写；宽度不一致统一为全角）。"""
    form_map = form_map if form_map is not None else build_form_map(analysis)
    text = analysis.text
    before: Dict[int, List[str]] = {}
    after: Dict[int, List[str]] = {}
    replace: Dict[int, str] = {}
    tail: List[str] = []

    for ev in scan_line(text):
        kind = ev[0]
        if kind == "pair":
            _, op, oc, cp, cc, family = ev
            if op in form_map:
                replace[op] = form_map[op][0]
                replace[cp] = form_map[op][1]
        elif kind == "cross":
            _, cp, cc, family, mp, mc, blockers = ev
            # 先闭合挡路符号（内层 -> 外层），再闭合匹配者，再重新打开挡路符号
            before.setdefault(cp, []).append(
                "".join(_mate_close(c) for _, c in reversed(blockers)))
            new_close = _mate_close(replace.get(mp, mc))
            if new_close != cc:
                replace[cp] = new_close
            after.setdefault(cp, []).append(
                "".join(c for _, c in blockers))
        elif kind == "unpaired_close":
            _, pos, ch, *_ = ev
            before.setdefault(pos, []).append(_mate_open(ch))
        # unpaired_open 在下面统一处理（事件顺序为栈顺序=外层->内层）

    leftover = [(ev[1], ev[2]) for ev in scan_line(text)
                if ev[0] == "unpaired_open"]
    for pos, ch in reversed(leftover):
        tail.append(_mate_close(replace.get(pos, ch)))

    out: List[str] = []
    for i, ch in enumerate(text):
        out.append("".join(before.get(i, ())))
        out.append(replace.get(i, ch))
        out.append("".join(after.get(i, ())))
    out.append("".join(before.get(len(text), ())))
    out.append("".join(tail))
    return "".join(out)


# ---------------------------------------------------------------- 标注

MARK_OPEN = "⟦"
MARK_CLOSE = "⟧"
CROSS_FLAG = "⚠"


def mark_line(analysis: LineAnalysis) -> str:
    """按 mark 策略输出：问题符号加 ⟦ ⟧ 标记，交叉点前加 ⚠。原文不增删。"""
    flagged: Dict[int, str] = {}
    for iss in analysis.issues:
        if iss.kind == "cross":
            flagged[iss.pos] = CROSS_FLAG + MARK_OPEN + iss.char + MARK_CLOSE
        elif iss.kind in ("unpaired_close", "unpaired_open"):
            flagged[iss.pos] = MARK_OPEN + iss.char + MARK_CLOSE
        elif iss.kind == "width_mismatch":
            for p in range(iss.pos, iss.end or iss.pos + 1):
                flagged.setdefault(p, MARK_OPEN + analysis.text[p] + MARK_CLOSE)
    return "".join(flagged.get(i, ch) for i, ch in enumerate(analysis.text))
