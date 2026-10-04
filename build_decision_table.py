#!/usr/bin/env python3
"""build_decision_table —— 决策表构建 + 新旧实现对拍 + 覆盖验证。

用法:
    python3 build_decision_table.py

产出:
    artifacts/decision_table.csv   全组合决策表（机读）
    artifacts/decision_table.md    全组合决策表（人读）
    artifacts/diff_report.md       逐组合对拍 + 边界扫描 + 模糊对拍 + 覆盖报告

任一组合对拍不一致、或覆盖验证失败，进程以非零码退出。
"""

import ast
import itertools
import os
import random
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

import enumerate as enum  # noqa: E402
import legacy_pricing  # noqa: E402
import pricing  # noqa: E402

ARTIFACTS = os.path.join(ROOT, "artifacts")

AMOUNT_CAP = 60000   # 无上限分段的扫描上限
QTY_CAP = 30
FUZZ_CASES = 5000
FUZZ_SEED = 20261004


def run(fn, args):
    """执行并归一化结果: ("ok", dict) 或 ("error", "TypeName: msg")。"""
    try:
        return ("ok", fn(*args))
    except Exception as exc:  # noqa: BLE001 - 对拍需要捕获一切
        return ("error", "%s: %s" % (type(exc).__name__, exc))


# ---------------------------------------------------------------- 覆盖追踪

class LineTracer:
    """统计指定文件在追踪期间被执行到的行号。"""

    def __init__(self, filename):
        self.filename = os.path.abspath(filename)
        self.hits = set()

    def _trace(self, frame, event, arg):
        if event == "line" and os.path.abspath(frame.f_code.co_filename) == self.filename:
            self.hits.add(frame.f_lineno)
        return self._trace

    def __enter__(self):
        sys.settrace(self._trace)
        return self

    def __exit__(self, *exc):
        sys.settrace(None)
        return False


def settle_statement_lines():
    """legacy_pricing.settle 函数体内全部语句行号（AST 静态提取）。

    排除 def 行与 docstring（它们在 import 期执行，追踪器启动前已完成）。
    """
    path = os.path.join(ROOT, "src", "legacy_pricing.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "settle":
            lines = {n.lineno for n in ast.walk(node) if isinstance(n, ast.stmt)}
            lines.discard(node.lineno)
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                lines.discard(node.body[0].lineno)
            return lines
    raise AssertionError("settle not found")


def annotated_unreachable_lines():
    """legacy 源码中的 UNREACHABLE 标注 -> {目标语句行号: 类别}。

    redundant：标注注释的下一行是永不执行的死语句，覆盖验证其命中数为 0。
    omission：遗漏的是"不存在的分支"，无对应死语句，仅登记供报告引用。
    """
    path = os.path.join(ROOT, "src", "legacy_pricing.py")
    lines = open(path, encoding="utf-8").read().splitlines()
    marks = {}
    for idx, text in enumerate(lines):
        if "UNREACHABLE(redundant)" in text:
            marks[idx + 2] = "redundant"  # 注释的下一行（1 起始行号）
        elif "UNREACHABLE(omission)" in text:
            marks.setdefault(("omission", idx + 1), "omission")
    return marks


def max_if_depth():
    """legacy settle 的最大 if 嵌套深度（AST 统计）。"""
    path = os.path.join(ROOT, "src", "legacy_pricing.py")
    tree = ast.parse(open(path, encoding="utf-8").read())

    def depth(node, level):
        best = level
        for child in ast.iter_child_nodes(node):
            best = max(best, depth(child, level + 1 if isinstance(child, ast.If) else level))
        return best

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "settle":
            return depth(node, 0)
    return 0


# ---------------------------------------------------------------- 对拍

def boundary_amounts(band):
    lo, hi = enum.AMOUNT_BANDS[band]
    hi = AMOUNT_CAP if hi is None else hi
    points = {lo - 1, lo, lo + 1, (lo + hi) // 2, hi - 1, hi, hi + 1}
    return sorted(p for p in points if 0 <= p <= AMOUNT_CAP)


def boundary_qtys(band):
    lo, hi = enum.QTY_BANDS[band]
    hi = QTY_CAP if hi is None else hi
    points = {lo - 1, lo, lo + 1, hi - 1, hi, hi + 1}
    return sorted(p for p in points if 1 <= p <= QTY_CAP)


def main():
    os.makedirs(ARTIFACTS, exist_ok=True)
    combos = enum.all_combos()
    tracer = LineTracer(os.path.join(ROOT, "src", "legacy_pricing.py"))

    rows = []          # 决策表行
    mismatches = []    # 对拍不一致
    boundary_cases = 0

    with tracer:
        # 1) 全组合代表值对拍 + 决策表
        for combo in combos:
            args = enum.representative(combo)
            old = run(legacy_pricing.settle, args)
            new = run(pricing.settle, args)
            if old != new:
                mismatches.append((combo, args, old, new))
            rows.append((combo, args, old))

        # 2) 边界扫描：每个组合内扫 amount/qty 边界点
        for combo in combos:
            a_band, q_band, vip, channel, region, coupon, birthday = combo
            for amount, qty in itertools.product(
                boundary_amounts(a_band), boundary_qtys(q_band)
            ):
                args = (amount, qty, vip, channel, region, coupon, birthday)
                old = run(legacy_pricing.settle, args)
                new = run(pricing.settle, args)
                boundary_cases += 1
                if old != new:
                    mismatches.append((combo, args, old, new))

        # 3) 随机模糊对拍
        rng = random.Random(FUZZ_SEED)
        for _ in range(FUZZ_CASES):
            args = (
                rng.randint(0, AMOUNT_CAP),
                rng.randint(1, QTY_CAP),
                rng.choice(enum.VIPS),
                rng.choice(enum.CHANNELS),
                rng.choice(enum.REGIONS),
                rng.choice(enum.COUPONS),
                rng.choice((False, True)),
            )
            old = run(legacy_pricing.settle, args)
            new = run(pricing.settle, args)
            if old != new:
                mismatches.append((enum.combo_of(*args), args, old, new))

        # 4) 非法输入对拍（覆盖全部校验分支）
        validation_cases = [
            (-1, 1, "gold", "app", "mainland", "none", False),
            (True, 1, "gold", "app", "mainland", "none", False),
            ("100", 1, "gold", "app", "mainland", "none", False),
            (100.5, 1, "gold", "app", "mainland", "none", False),
            (None, 1, "gold", "app", "mainland", "none", False),
            (1000, 0, "gold", "app", "mainland", "none", False),
            (1000, -3, "gold", "app", "mainland", "none", False),
            (1000, True, "gold", "app", "mainland", "none", False),
            (1000, "2", "gold", "app", "mainland", "none", False),
            (1000, 1, "platinum", "app", "mainland", "none", False),
            (1000, 1, "gold", "pos", "mainland", "none", False),
            (1000, 1, "gold", "app", "overseas", "none", False),
            (1000, 1, "gold", "app", "mainland", "voucher", False),
            (4999, 1, "gold", "app", "mainland", "cash", False),
            (19999, 1, "gold", "app", "mainland", "gift", False),
        ]
        for args in validation_cases:
            old = run(legacy_pricing.settle, args)
            new = run(pricing.settle, args)
            if old != new:
                mismatches.append((("validation",), args, old, new))

    # 5) 覆盖验证
    stmts = settle_statement_lines()
    marks = annotated_unreachable_lines()
    redundant_lines = {l for l in marks if isinstance(l, int)}
    dead_hit = sorted(l for l in redundant_lines
                      if l in tracer.hits and l in settle_statement_lines())
    missed = sorted(l for l in stmts if l not in tracer.hits
                    and l not in redundant_lines)

    # ---------------------------------------------------------------- 输出
    write_decision_table(rows)
    write_diff_report(
        rows, mismatches, boundary_cases, len(validation_cases),
        tracer, stmts, marks, dead_hit, missed
    )

    ok = not mismatches and not dead_hit and not missed
    print("combos=%d boundary=%d fuzz=%d mismatches=%d dead_hit=%d missed_lines=%d"
          % (len(rows), boundary_cases, FUZZ_CASES,
             len(mismatches), len(dead_hit), len(missed)))
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def fmt_outcome(outcome):
    kind, payload = outcome
    if kind == "error":
        return "ERROR", payload, "", "", "", ""
    return ("OK", "", payload["payable"], payload["shipping"],
            payload["discount"], "%s/%s" % (payload["gift"], payload["label"]))


def write_decision_table(rows):
    csv_path = os.path.join(ARTIFACTS, "decision_table.csv")
    with open(csv_path, "w", encoding="utf-8") as fh:
        fh.write("combo_id,amount_band,qty_band,vip,channel,region,coupon,birthday,"
                 "rep_amount,rep_qty,result,error,payable,shipping,discount,gift_label\n")
        for idx, (combo, args, outcome) in enumerate(rows, start=1):
            result, error, payable, shipping, discount, gift_label = fmt_outcome(outcome)
            fh.write("%d,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n" % (
                idx, combo[0], combo[1], combo[2], combo[3], combo[4], combo[5],
                combo[6], args[0], args[1], result, error, payable, shipping,
                discount, gift_label))

    md_path = os.path.join(ARTIFACTS, "decision_table.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("# 决策表（由 build_decision_table.py 自动生成，勿手改）\n\n")
        fh.write("输入维度：amount_band(0:[0,4999] 1:[5000,9999] 2:[10000,19999] 3:[20000,))、"
                 "qty_band(0:[1,9] 1:[10,19] 2:[20,))、vip、channel、region、coupon、birthday。"
                 "代表值取段内中点（无上限段取下限+1）。\n\n")
        fh.write("| # | a_band | q_band | vip | channel | region | coupon | birthday "
                 "| rep(amount,qty) | 结果 | payable | shipping | discount | gift/label |\n")
        fh.write("|---|--------|--------|-----|---------|--------|--------|----------"
                 "|-----------------|------|---------|----------|----------|------------|\n")
        for idx, (combo, args, outcome) in enumerate(rows, start=1):
            result, error, payable, shipping, discount, gift_label = fmt_outcome(outcome)
            note = error if result == "ERROR" else ""
            fh.write("| %d | %s | %s | %s | %s | %s | %s | %s | (%s,%s) | %s %s | %s | %s | %s | %s |\n"
                     % (idx, combo[0], combo[1], combo[2], combo[3], combo[4], combo[5],
                        combo[6], args[0], args[1], result, note,
                        payable, shipping, discount, gift_label))


def write_diff_report(rows, mismatches, boundary_cases, validation_cases,
                      tracer, stmts, marks, dead_hit, missed):
    bad_keys = {(c, a) for c, a, _, _ in mismatches}
    path = os.path.join(ARTIFACTS, "diff_report.md")
    errors = sum(1 for _, _, o in rows if o[0] == "error")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# 对拍报告（由 build_decision_table.py 自动生成）\n\n")
        fh.write("## 概览\n\n")
        fh.write("- legacy 最大 if 嵌套深度：**%d 层**\n" % max_if_depth())
        fh.write("- 条件组合总数：**%d**（4 amount 段 × 3 qty 段 × 3 vip × 2 channel "
                 "× 2 region × 3 coupon × 2 birthday）\n" % len(rows))
        fh.write("- 其中抛错组合：%d，正常返回组合：%d\n" % (errors, len(rows) - errors))
        fh.write("- 边界扫描用例：%d（每组合扫 amount/qty 段界 ±1 及中点）\n" % boundary_cases)
        fh.write("- 随机模糊用例：%d（种子 %d）\n" % (FUZZ_CASES, FUZZ_SEED))
        fh.write("- 非法输入用例：%d（类型错误 / 越界 / 未知枚举 / 券门槛不足）\n"
                 % validation_cases)
        fh.write("- 不一致用例：**%d**\n\n" % len(mismatches))

        fh.write("## 逐组合对拍\n\n")
        fh.write("| # | 组合 (a_band,q_band,vip,channel,region,coupon,birthday) | 代表值 | 结果 |\n")
        fh.write("|---|----------------------------------------------------------|--------|------|\n")
        for idx, (combo, args, outcome) in enumerate(rows, start=1):
            result, error, *_ = fmt_outcome(outcome)
            status = "MISMATCH" if (combo, args) in bad_keys else "MATCH"
            fh.write("| %d | %s | %s | %s %s |\n"
                     % (idx, combo, (args[0], args[1]), result,
                        ("%s %s" % (error, status)) if error else status))
        fh.write("\n")

        fh.write("## 覆盖验证（legacy_pricing.settle）\n\n")
        fh.write("- 函数体语句行：%d 行，对拍期间全部被执行%s\n"
                 % (len(stmts), "（遗漏 0 行）" if not missed
                     else "，未覆盖行: %s" % missed))
        redundant = sorted(l for l in marks if isinstance(l, int))
        omissions = sorted(k[1] for k in marks if not isinstance(k, int))
        fh.write("- UNREACHABLE(redundant) 死语句：%d 处（L%s），对拍期间命中 %d 处%s\n"
                 % (len(redundant), ", ".join(map(str, redundant)),
                    len(dead_hit), "（符合预期）" if not dead_hit
                    else "，异常命中: %s" % dead_hit))
        fh.write("- UNREACHABLE(omission) 遗漏：%d 处（注释位于 L%s）\n"
                 % (len(omissions), ", ".join(map(str, omissions))))
        fh.write("\n详细分类说明见 docs/unreachable_analysis.md。\n")

        if mismatches:
            fh.write("\n## 不一致明细\n\n")
            for combo, args, old, new in mismatches[:50]:
                fh.write("- 组合 %s 入参 %s：legacy=%r pricing=%r\n"
                         % (combo, args, old, new))


if __name__ == "__main__":
    sys.exit(main())
