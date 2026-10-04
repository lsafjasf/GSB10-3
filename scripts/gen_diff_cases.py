#!/usr/bin/env python3
"""生成求值对拍数据 data/eval_cases.json。

对拍思路：这里内置一个独立的 oracle 求值器（调度场算法，不 import 库代码），
随机生成无环公式工作簿，oracle 算出期望值写进 JSON；
测试侧再用 formulas 库重算并逐一比对，从而交叉验证库的实现。
"""

import json
import math
import os
import random
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from formulas.cellref import num_to_col  # 仅用于地址格式化

TOKEN_RE = re.compile(r"""
    (?P<num>\d+(?:\.\d+)?)
  | (?P<func>SUM)
  | (?P<sheet>[A-Za-z_][A-Za-z0-9_]*)(?=!)
  | (?P<cell>\$?[A-Z]{1,3}\$?[1-9][0-9]*)
  | (?P<op>[+\-*/():,!])
""", re.VERBOSE)


def oracle_eval(formula, constants):
    """独立 oracle：只支持数字、四则、括号、单元格/跨表引用、SUM(区域)。

    constants: {(sheet, 'A1'): float}
    """
    tokens = []
    i = 0
    while i < len(formula):
        m = TOKEN_RE.match(formula, i)
        if not m:
            raise ValueError("oracle 无法解析: %r @%d" % (formula, i))
        tokens.append(m.group(0))
        i = m.end()

    def cell_value(sheet, addr):
        return constants[(sheet, addr)]

    pos = [0]

    def peek():
        return tokens[pos[0]] if pos[0] < len(tokens) else None

    def eat():
        t = tokens[pos[0]]
        pos[0] += 1
        return t

    def parse_expr():
        v = parse_term()
        while peek() in ("+", "-"):
            op = eat()
            r = parse_term()
            v = v + r if op == "+" else v - r
        return v

    def parse_term():
        v = parse_factor()
        while peek() in ("*", "/"):
            op = eat()
            r = parse_factor()
            v = v * r if op == "*" else v / r
        return v

    def parse_factor():
        t = eat()
        if t == "-":
            return -parse_factor()
        if t == "+":
            return parse_factor()
        if t == "(":
            v = parse_expr()
            assert eat() == ")"
            return v
        if t == "SUM":
            assert eat() == "("
            total = 0.0
            while True:
                total += parse_range_or_expr()
                if peek() == ",":
                    eat()
                    continue
                break
            assert eat() == ")"
            return total
        if re.fullmatch(r"\d+(\.\d+)?", t):
            return float(t)
        # 引用，可能带表名前缀（此时 t 是表名，后面是 !）
        sheet = "S1"
        if peek() == "!":
            eat()
            sheet = t
            t = eat()
        return cell_value(sheet, t)

    def parse_range_or_expr():
        # 区域 A1:B2 或普通表达式
        save = pos[0]
        t = eat()
        sheet = "S1"
        if peek() == "!":
            eat()
            sheet = t
            t = eat()
        if re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]*", t) and peek() == ":":
            eat()
            t2 = eat()
            c1, r1 = re.match(r"([A-Z]+)(\d+)", t).groups()
            c2, r2 = re.match(r"([A-Z]+)(\d+)", t2).groups()
            from formulas.cellref import col_to_num
            c1, c2 = col_to_num(c1), col_to_num(c2)
            r1, r2 = int(r1), int(r2)
            total = 0.0
            for r in range(min(r1, r2), max(r1, r2) + 1):
                for c in range(min(c1, c2), max(c1, c2) + 1):
                    total += constants.get((sheet, "%s%d" % (num_to_col(c), r)), 0.0)
            return total
        pos[0] = save
        return parse_expr()

    result = parse_expr()
    assert pos[0] == len(tokens), "oracle 有剩余记号"
    return result


def gen_case(rng, name, n_const=12, n_formula=14):
    """生成一个无环用例：公式只引用已赋值的单元格，天然是 DAG。"""
    constants = {}
    sheets = {"S1": {}, "S2": {}}
    known = []  # (sheet, addr) 已有确定值的单元格

    def put_const(sheet, addr, v):
        constants[(sheet, addr)] = v
        sheets[sheet][addr] = v
        known.append((sheet, addr))

    for i in range(n_const):
        sheet = "S1" if i % 3 else "S2"
        addr = "%s%d" % (num_to_col(1 + i % 6), 1 + i // 6)
        put_const(sheet, addr, round(rng.uniform(-50, 50), 2))

    def gen_expr(depth):
        if depth <= 0 or rng.random() < 0.35:
            if rng.random() < 0.5 and known:
                sheet, addr = rng.choice(known)
                ref = addr if sheet == "S1" else "%s!%s" % (sheet, addr)
                return ref, constants[(sheet, addr)]
            v = round(rng.uniform(-20, 20), 2)
            if v == 0:
                v = 1.0
            return repr(v), v
        op = rng.choice("+-*/")
        ls, lv = gen_expr(depth - 1)
        rs, rv = gen_expr(depth - 1)
        if op == "/" and (rv == 0 or abs(rv) < 1e-9):
            rs, rv = "1.0", 1.0
        if op == "+":
            val = lv + rv
        elif op == "-":
            val = lv - rv
        elif op == "*":
            val = lv * rv
        else:
            val = lv / rv
        if not math.isfinite(val) or abs(val) > 1e12:
            return "1.0", 1.0
        return "(%s%s%s)" % (ls, op, rs), val

    formulas = {}
    for i in range(n_formula):
        addr = "%s%d" % (num_to_col(7 + i % 4), 1 + i // 4)
        const_cells = [k for k in known if k in constants and not isinstance(
            sheets[k[0]][k[1]], dict)]
        if rng.random() < 0.25 and len(const_cells) >= 4:
            # SUM 区域：只取常量区，保证不会套住任何公式格（无环）
            (s1, a1), (s2, a2) = rng.sample(const_cells, 2)
            if s1 != s2:
                s2, a2 = s1, a1
            f = "SUM(%s:%s)" % (a1, a2) if s1 == "S1" else "SUM(%s!%s:%s)" % (s1, a1, a2)
            text = "=" + f
        else:
            expr, _ = gen_expr(rng.randint(1, 3))
            text = "=" + expr
        value = oracle_eval(text[1:], constants)
        assert math.isfinite(value)
        sheets["S1"][addr] = {"formula": text}
        constants[("S1", addr)] = value
        known.append(("S1", addr))
        formulas["S1!%s" % addr] = value

    return {"name": name, "sheets": sheets, "expect": formulas}


def main():
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 20261004
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    rng = random.Random(seed)
    cases = [gen_case(rng, "diff-%03d" % i) for i in range(count)]
    out = {
        "description": "求值对拍数据：由 scripts/gen_diff_cases.py 用独立 oracle 生成",
        "seed": seed,
        "cases": cases,
    }
    path = os.path.join(os.path.dirname(__file__), "..", "data", "eval_cases.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    n_formula = sum(len(c["expect"]) for c in cases)
    print("生成 %d 个用例 / %d 个公式 -> %s" % (count, n_formula, os.path.abspath(path)))


if __name__ == "__main__":
    main()
