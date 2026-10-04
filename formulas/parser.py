"""公式词法分析 + 递归下降解析器。

支持语法：
  expr      := 比较表达式
  comparison:= concat (('=' | '<>' | '<' | '<=' | '>' | '>=') concat)*
  concat    := term (('+'|'-') term ... )，字符串连接用 '&'
  term      := factor (('*'|'/') factor)*
  power     := unary ('^' unary)*
  unary     := ('-'|'+') unary | postfix
  postfix   := atom ('%')*
  atom      := 数字 | "字符串" | 错误字面量 | TRUE/FALSE
             | 单元格引用 | 区域引用 | 函数名 '(' 参数 ')' | '(' expr ')'

引用：A1、A1:C3、Sheet2!B2、'My Sheet'!A1:C3。
"""

from .cellref import CellRef, Range
from .errors import ERROR_LITERALS

# AST 用轻量元组表示：
#   ("num", float) ("str", str) ("bool", bool) ("error", code)
#   ("ref", CellRef) ("range", Range)
#   ("unary", op, node) ("bin", op, left, right)
#   ("call", name, [arg, ...])
#   ("range_arg", Range) 只允许作为函数参数

_KEYWORDS = {"TRUE": ("bool", True), "FALSE": ("bool", False)}


class ParseError(Exception):
    pass


class Token:
    __slots__ = ("kind", "value")

    def __init__(self, kind, value):
        self.kind = kind
        self.value = value

    def __repr__(self):
        return "Token(%s, %r)" % (self.kind, self.value)


_CMP = ("<>", "<=", ">=", "=", "<", ">")


class Lexer:
    def __init__(self, text):
        self.text = text
        self.pos = 0

    def _read_name(self):
        start = self.pos
        n = len(self.text)
        while self.pos < n and (self.text[self.pos].isalnum() or self.text[self.pos] == "_"):
            self.pos += 1
        return self.text[start:self.pos]

    def _read_cell_token(self, sheet=None):
        name = self._read_name()
        # 可能是区域 A1:B2
        if self.pos < len(self.text) and self.text[self.pos] == ":":
            self.pos += 1
            second = self._read_name()
            rng = Range.parse("%s:%s" % (name, second))
            rng.sheet = sheet
            return Token("RANGE", rng)
        ref = CellRef.parse(name)
        ref.sheet = sheet
        return Token("CELL", ref)

    def tokens(self):
        s = self.text
        n = len(s)
        out = []
        while self.pos < n:
            ch = s[self.pos]
            if ch.isspace():
                self.pos += 1
                continue
            if ch == '"':
                self.pos += 1
                buf = []
                while self.pos < n:
                    c = s[self.pos]
                    if c == '"':
                        if self.pos + 1 < n and s[self.pos + 1] == '"':
                            buf.append('"')
                            self.pos += 2
                            continue
                        self.pos += 1
                        break
                    buf.append(c)
                    self.pos += 1
                else:
                    raise ParseError("字符串缺少结束引号")
                out.append(Token("STRING", "".join(buf)))
                continue
            if ch == "'":
                # 带引号的工作表限定
                self.pos += 1
                buf = []
                while self.pos < n:
                    c = s[self.pos]
                    if c == "'":
                        if self.pos + 1 < n and s[self.pos + 1] == "'":
                            buf.append("'")
                            self.pos += 2
                            continue
                        self.pos += 1
                        break
                    buf.append(c)
                    self.pos += 1
                if self.pos >= n or s[self.pos] != "!":
                    raise ParseError("工作表限定后缺少 !")
                self.pos += 1
                out.append(self._read_cell_token("".join(buf)))
                continue
            if ch.isdigit() or (ch == "." and self.pos + 1 < n and s[self.pos + 1].isdigit()):
                start = self.pos
                dot = False
                while self.pos < n and (s[self.pos].isdigit() or s[self.pos] == "."):
                    if s[self.pos] == ".":
                        if dot:
                            raise ParseError("非法数字")
                        dot = True
                    self.pos += 1
                # 科学计数法
                if self.pos < n and s[self.pos] in "eE":
                    save = self.pos
                    self.pos += 1
                    if self.pos < n and s[self.pos] in "+-":
                        self.pos += 1
                    if self.pos < n and s[self.pos].isdigit():
                        while self.pos < n and s[self.pos].isdigit():
                            self.pos += 1
                    else:
                        self.pos = save
                out.append(Token("NUMBER", float(s[start:self.pos])))
                continue
            if ch == "#":
                code = None
                for lit in ERROR_LITERALS:
                    if s.startswith(lit, self.pos):
                        code = lit
                        break
                if code is None:
                    raise ParseError("无法识别的错误字面量")
                self.pos += len(code)
                out.append(Token("ERROR", code))
                continue
            if ch.isalpha() or ch == "_":
                name = self._read_name()
                upper = name.upper()
                if upper in _KEYWORDS:
                    kind, val = _KEYWORDS[upper]
                    out.append(Token(kind.upper(), val))
                    continue
                if self.pos < n and s[self.pos] == "!":
                    # 不带引号的工作表限定 Sheet1!A1
                    self.pos += 1
                    out.append(self._read_cell_token(name))
                    continue
                if self.pos < n and s[self.pos] == "(":
                    out.append(Token("FUNC", upper))
                    continue
                # 裸名字：单元格引用 / 区域 / 未定义名称
                try:
                    ref = CellRef.parse(name)
                except ValueError:
                    out.append(Token("NAME", upper))
                    continue
                if self.pos < n and s[self.pos] == ":":
                    self.pos += 1
                    second = self._read_name()
                    out.append(Token("RANGE", Range.parse("%s:%s" % (name, second))))
                else:
                    out.append(Token("CELL", ref))
                continue
            two = s[self.pos:self.pos + 2]
            if two in _CMP:
                out.append(Token("CMP", two))
                self.pos += 2
                continue
            if ch in "=<>":
                out.append(Token("CMP", ch))
                self.pos += 1
                continue
            if ch in "+-*/^&%(),":
                out.append(Token(ch, ch))
                self.pos += 1
                continue
            raise ParseError("无法识别的字符 %r" % ch)
        out.append(Token("EOF", None))
        return out


class Parser:
    def __init__(self, text):
        self.text = text
        self.toks = Lexer(text).tokens()
        self.i = 0

    @property
    def cur(self):
        return self.toks[self.i]

    def advance(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def expect(self, kind):
        if self.cur.kind != kind:
            raise ParseError("期望 %s，实际 %r" % (kind, self.cur))
        return self.advance()

    def parse(self):
        node = self.comparison()
        if self.cur.kind != "EOF":
            raise ParseError("公式末尾存在多余内容: %r" % self.cur.value)
        return node

    def comparison(self):
        node = self.concat()
        while self.cur.kind == "CMP":
            op = self.advance().value
            node = ("bin", op, node, self.concat())
        return node

    def concat(self):
        node = self.addsub()
        while self.cur.kind == "&":
            self.advance()
            node = ("bin", "&", node, self.addsub())
        return node

    def addsub(self):
        node = self.muldiv()
        while self.cur.kind in ("+", "-"):
            op = self.advance().kind
            node = ("bin", op, node, self.muldiv())
        return node

    def muldiv(self):
        node = self.power()
        while self.cur.kind in ("*", "/"):
            op = self.advance().kind
            node = ("bin", op, node, self.power())
        return node

    def power(self):
        node = self.unary()
        while self.cur.kind == "^":
            self.advance()
            node = ("bin", "^", node, self.unary())
        return node

    def unary(self):
        if self.cur.kind in ("+", "-"):
            op = self.advance().kind
            return ("unary", op, self.unary())
        return self.postfix()

    def postfix(self):
        node = self.atom()
        while self.cur.kind == "%":
            self.advance()
            node = ("bin", "/", node, ("num", 100.0))
        return node

    def atom(self):
        t = self.cur
        if t.kind == "NUMBER":
            self.advance()
            return ("num", t.value)
        if t.kind == "STRING":
            self.advance()
            return ("str", t.value)
        if t.kind == "BOOL":
            self.advance()
            return ("bool", t.value)
        if t.kind == "ERROR":
            self.advance()
            return ("error", t.value)
        if t.kind == "CELL":
            self.advance()
            return ("ref", t.value)
        if t.kind == "RANGE":
            raise ParseError("区域引用只能作为聚合函数的参数")
        if t.kind == "NAME":
            self.advance()
            return ("error", "#NAME?")
        if t.kind == "(":
            self.advance()
            node = self.comparison()
            self.expect(")")
            return node
        if t.kind == "FUNC":
            fname = self.advance().value
            self.expect("(")
            args = []
            if self.cur.kind != ")":
                while True:
                    if self.cur.kind == "RANGE":
                        args.append(("range", self.advance().value))
                    else:
                        args.append(self.comparison())
                    if self.cur.kind == ",":
                        self.advance()
                        continue
                    break
            self.expect(")")
            return ("call", fname, args)
        raise ParseError("意外的记号: %r" % t)


def parse_formula(text):
    """解析公式（不含开头的 '='），返回 AST。公式必须以 '=' 开头时也兼容。"""
    if text.startswith("="):
        text = text[1:]
    return Parser(text).parse()


def collect_refs(node, current_sheet=None):
    """遍历 AST，返回 (cells, ranges)：AST 引用到的单元格与区域。

    返回的单元格为规范化三元组 (sheet, row, col)。
    """
    cells = []
    ranges = []

    def walk(n, top=False):
        kind = n[0]
        if kind == "ref":
            cells.append(n[1].resolve(current_sheet))
        elif kind == "range":
            rng = n[1]
            ranges.append(rng)
            cells.extend(rng.addresses(current_sheet))
        elif kind == "bin":
            walk(n[2])
            walk(n[3])
        elif kind == "unary":
            walk(n[2])
        elif kind == "call":
            for arg in n[2]:
                walk(arg)

    walk(node)
    # 去重但保持顺序
    seen = set()
    uniq_cells = []
    for c in cells:
        if c not in seen:
            seen.add(c)
            uniq_cells.append(c)
    return uniq_cells, ranges
