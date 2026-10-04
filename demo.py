# -*- coding: utf-8 -*-
"""演示/样例生成：配对冲突、全半角转换对照、边界用例。

运行：python3 demo.py
会同时在 samples/ 下生成：
  conflicts.txt   配对冲突样例（含列尺、冲突位置、两种策略结果）
  conversions.tsv 全/半角转换前后对照数据
  edge_cases.txt  边界用例与处理结果
"""

import os

from punctfix import analyze_line, fix_line, mark_line, normalize
from punctfix.width import normalize_line

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "samples")


def ruler(text):
    """生成 1-based 列尺（每字符一列，两位数取个位以保持对齐）。"""
    top = "".join(str((i + 1) // 10 or " ") for i in range(len(text)))
    bot = "".join(str((i + 1) % 10) for i in range(len(text)))
    return top + "\n" + bot


def section(title):
    line = "=" * 68
    return f"{line}\n{title}\n{line}"


# ------------------------------------------------------------ 配对冲突

CONFLICT_CASES = [
    "（外层 [ 中层 ） 交叉 ]",        # 方括号挡在圆括号中间
    "《书名〈小节》收尾〉",            # 双书名号内嵌单书名号，提前闭合
    "“他说 ‘你好 ” 微笑 ’",          # 弯引号交叉
    "（a [ b { c ] d } e ）",        # 多重嵌套中的交叉
]


def render_conflicts():
    blocks = [section("一、配对冲突样例（列尺为 1-based，0-based 见说明）"),
              "标记约定： ⚠⟦…⟧ = 交叉/未配对点；fix = 自动补齐后的平衡文本"]
    for idx, case in enumerate(CONFLICT_CASES, 1):
        la = analyze_line(case)
        blocks.append(f"\n--- 样例 {idx} ---")
        blocks.append("原文   : " + case)
        blocks.append("列尺   :")
        blocks.extend("         " + r for r in ruler(case).splitlines())
        for iss in la.issues:
            loc = f"行{iss.line} 列{iss.column}"
            if iss.blockers:
                bs = "、".join(f"列{p + 1}「{c}」" for p, c in iss.blockers)
                blocks.append(f"冲突   : [{loc}] 符号「{iss.char}」与挡路符号 {bs} 交叉")
            else:
                blocks.append(f"问题   : [{loc}] {iss.detail}")
        blocks.append("mark   : " + mark_line(la))
        blocks.append("fix    : " + fix_line(la))
    return "\n".join(blocks) + "\n"


# ------------------------------------------------------------ 转换对照

CONVERSION_CASES = [
    ("共３００人", "数字"),
    ("价格是３．５０元", "小数"),
    ("共１，０００名，１２：３０出发", "千分位/时间"),
    ("增长５０％，下降－３℃", "百分号/负号（℃ 无半角对应，保留）"),
    ("重量５ｋｇ，距离１０ｋｍ", "数字+单位补空格"),
    ("来自Ｕ．Ｓ．Ａ．的记者", "英文缩写（带点）"),
    ("Ａｐｐｌｅ发布ｉＰｈｏｎｅ １６", "全角拉丁字母"),
    ("ｈｉ！ｔｈｅｒｅ", "ASCII 上下文标点"),
    ("有效期１～１０天", "数字区间保留全角波浪号"),
    ("你好，世界。真的！", "CJK 标点保留"),
    ("　　首行缩进　正文", "行首 U+3000 缩进保留，其余转半角"),
    ("版本ｖ１．２，编号Ｎｏ．００１", "版本号/编号"),
]


def render_conversions():
    rows = ["序号\t场景\t转换前\t转换后\t命中规则（字符级）"]
    detail_lines = []
    for idx, (case, scene) in enumerate(CONVERSION_CASES, 1):
        after, changes = normalize_line(case)
        rules = " ".join(f"{c.before or '∅'}->{c.after}[{c.rule}]"
                         for c in changes) or "（无改动）"
        rows.append(f"{idx}\t{scene}\t{case}\t{after}\t{rules}")
        detail_lines.append(f"{idx:>2}. {scene}")
        detail_lines.append(f"    前: {case}")
        detail_lines.append(f"    后: {after}")
        detail_lines.append(f"    改动: {rules}")
    tsv = "\n".join(rows) + "\n"

    human = [section("二、全/半角转换前后对照数据（同数据另存 conversions.tsv）")]
    human.extend(detail_lines)
    return tsv, "\n".join(human) + "\n"


# ------------------------------------------------------------ 边界用例

EDGE_CASES = [
    "",
    "没有任何标点的纯文本",
    "（（（",
    "））",
    "it's O'Reilly（撇号不是引号）",
    'say "hello" to him',
    "“单行引号未闭合",
    "（half) 宽度不一致",
    "《一〈二「三」二〉一》连续四层",
    "「『引号』即括号」",
    "第一行（未闭合\n第二行）独立处理",
    "　　缩进与３．１４混合（缺闭",
]


def render_edge():
    out = [section("三、边界用例（fix 策略结果 + 问题标注）")]
    for case in EDGE_CASES:
        r = normalize(case if case else "", strategy="fix")
        out.append(f"\n原文   : {case!r}")
        out.append(f"fix    : {r.text!r}")
        if r.issues:
            for i in r.issues:
                out.append(f"  - 行{i.line}列{i.column} {i.kind}: {i.detail}")
        else:
            out.append("  - 无配对问题")
    return "\n".join(out) + "\n"


def main():
    os.makedirs(OUT, exist_ok=True)
    conflicts = render_conflicts()
    tsv, conv_human = render_conversions()
    edge = render_edge()

    with open(os.path.join(OUT, "conflicts.txt"), "w", encoding="utf-8") as f:
        f.write(conflicts)
    with open(os.path.join(OUT, "conversions.tsv"), "w", encoding="utf-8") as f:
        f.write(tsv)
    with open(os.path.join(OUT, "conversions.txt"), "w", encoding="utf-8") as f:
        f.write(conv_human)
    with open(os.path.join(OUT, "edge_cases.txt"), "w", encoding="utf-8") as f:
        f.write(edge)

    print(conflicts)
    print(conv_human)
    print(edge)
    print(f"样例文件已写入: {OUT}")


if __name__ == "__main__":
    main()
