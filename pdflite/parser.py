# -*- coding: utf-8 -*-
"""手写递归下降解析器：在字节流上解析 PDF 语法对象与间接对象。

设计要点：
- 全程按字节操作，offset 即文件偏移，供 xref 校验与顺序扫描复用。
- 解析失败抛 PdfParseError，绝不静默吞错。
- 流(stream)先按 /Length 取数据，长度不可信时回退到 endstream 搜索。
"""

import re

from .objects import Name, Ref, PdfDict, PdfStream, IndirectObject

WS = b"\x00\t\n\x0c\r "
DELIMS = b"()<>[]{}/%"

_NUMBER_RE = re.compile(rb"[+-]?(?:\d+\.\d*|\.\d+|\d+)")
_NAME_RE = re.compile(rb"/[^\x00\t\n\x0c\r ()<>\[\]{}/%]*")
_HEX_RE = re.compile(rb"[0-9A-Fa-f\s]*")


class PdfParseError(Exception):
    def __init__(self, msg, offset=-1):
        super().__init__(msg)
        self.offset = offset


class Parser:
    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos
        self._stack = []

    # ---------- 基础游标操作 ----------
    def _skip_ws(self):
        d = self.data
        n = len(d)
        p = self.pos
        while p < n:
            c = d[p]
            if c in WS:
                p += 1
            elif c == 0x25:  # '%' 注释直到行尾
                while p < n and d[p] not in b"\r\n":
                    p += 1
            else:
                break
        self.pos = p

    def _peek(self):
        if self._stack:
            return self._stack[-1]
        tok = self._next_token()
        self._stack.append(tok)
        return tok

    def _pop(self):
        if self._stack:
            return self._stack.pop()
        return self._next_token()

    def _expect_keyword(self, word: bytes):
        tok = self._pop()
        if tok != ("kw", word):
            raise PdfParseError("期望关键字 %r，实际 %r" % (word, tok), self.pos)

    # ---------- 词法 ----------
    def _next_token(self):
        self._skip_ws()
        d = self.data
        p = self.pos
        if p >= len(d):
            raise PdfParseError("意外到达文件末尾", p)
        c = d[p:p + 1]
        if c == b"<":
            if d[p:p + 2] == b"<<":
                self.pos = p + 2
                return ("sym", "<<")
            return ("hexstr", self._read_hexstring(p))
        if c == b">":
            if d[p:p + 2] == b">>":
                self.pos = p + 2
                return ("sym", ">>")
            raise PdfParseError("孤立的 '>'", p)
        if c == b"[":
            self.pos = p + 1
            return ("sym", "[")
        if c == b"]":
            self.pos = p + 1
            return ("sym", "]")
        if c == b"(":
            return ("str", self._read_literal(p))
        if c == b"/":
            m = _NAME_RE.match(d, p)
            self.pos = m.end()
            return ("name", self._decode_name(m.group()))
        m = _NUMBER_RE.match(d, p)
        if m:
            self.pos = m.end()
            text = m.group()
            if b"." in text:
                return ("num", float(text))
            return ("num", int(text))
        # 关键字：读到空白或定界符为止
        q = p
        n = len(d)
        while q < n and d[q] not in WS and d[q] not in DELIMS:
            q += 1
        if q == p:
            raise PdfParseError("无法识别的字节 0x%02x" % d[p], p)
        self.pos = q
        return ("kw", d[p:q])

    def _read_hexstring(self, p):
        m = _HEX_RE.match(self.data, p + 1)
        body = m.group()
        end = m.end()
        if end >= len(self.data) or self.data[end:end + 1] != b">":
            raise PdfParseError("十六进制串未闭合", p)
        self.pos = end + 1
        body = re.sub(rb"\s+", b"", body)
        if len(body) % 2:
            body += b"0"
        try:
            return bytes.fromhex(body.decode("ascii"))
        except ValueError:
            raise PdfParseError("十六进制串含非法字符", p)

    def _read_literal(self, p):
        d = self.data
        n = len(d)
        out = bytearray()
        depth = 1
        i = p + 1
        while i < n:
            c = d[i]
            if c == 0x5C:  # 反斜杠转义
                i += 1
                if i >= n:
                    break
                e = d[i]
                mapping = {0x6E: 0x0A, 0x72: 0x0D, 0x74: 0x09,
                           0x62: 0x08, 0x66: 0x0C}
                if e in mapping:
                    out.append(mapping[e])
                    i += 1
                elif e in b"\r\n":
                    # 行延续：吃掉 \r\n 或单个换行
                    if e == 0x0D and i + 1 < n and d[i + 1] == 0x0A:
                        i += 2
                    else:
                        i += 1
                elif 0x30 <= e <= 0x37:  # 八进制，最多 3 位
                    j = i
                    oct_digits = []
                    while j < n and len(oct_digits) < 3 and 0x30 <= d[j] <= 0x37:
                        oct_digits.append(d[j])
                        j += 1
                    out.append(int(bytes(oct_digits), 8) & 0xFF)
                    i = j
                else:
                    out.append(e)
                    i += 1
            elif c == 0x28:  # (
                depth += 1
                out.append(c)
                i += 1
            elif c == 0x29:  # )
                depth -= 1
                if depth == 0:
                    self.pos = i + 1
                    return bytes(out)
                out.append(c)
                i += 1
            else:
                out.append(c)
                i += 1
        raise PdfParseError("文字串未闭合", p)

    def _decode_name(self, raw: bytes) -> Name:
        out = bytearray()
        i = 1  # 跳过 '/'
        while i < len(raw):
            if raw[i:i + 1] == b"#" and i + 2 < len(raw):
                try:
                    out.append(int(raw[i + 1:i + 3], 16))
                    i += 3
                    continue
                except ValueError:
                    pass
            out.append(raw[i])
            i += 1
        return Name("/" + out.decode("latin-1"))

    # ---------- 语法 ----------
    def parse_value(self):
        tok = self._pop()
        kind, val = tok
        if kind == "sym":
            if val == "<<":
                return self._parse_dict()
            if val == "[":
                return self._parse_array()
            raise PdfParseError("意外的符号 %r" % val, self.pos)
        if kind == "name":
            return val
        if kind in ("str", "hexstr"):
            return val
        if kind == "num":
            # 引用前看：int int R
            if isinstance(val, int):
                save_stack = list(self._stack)
                save_pos = self.pos
                ref = self._try_ref(val)
                if ref is not None:
                    return ref
                self._stack = save_stack
                self.pos = save_pos
            return val
        if kind == "kw":
            if val == b"true":
                return True
            if val == b"false":
                return False
            if val == b"null":
                return None
            raise PdfParseError("值位置出现关键字 %r" % val, self.pos)
        raise PdfParseError("无法解析的记号 %r" % (tok,), self.pos)

    def _parse_dict(self):
        d = PdfDict()
        while True:
            tok = self._pop()
            if tok == ("sym", ">>"):
                return d
            if tok[0] != "name":
                raise PdfParseError("字典键必须是名称，实际 %r" % (tok,), self.pos)
            d[tok[1]] = self.parse_value()

    def _parse_array(self):
        arr = []
        while True:
            tok = self._peek()
            if tok == ("sym", "]"):
                self._pop()
                return arr
            arr.append(self.parse_value())

    def _try_ref(self, num):
        """游标位于第二个记号处时尝试识别 "gen R"，成功返回 Ref。"""
        try:
            t2 = self._pop()
        except PdfParseError:
            return None
        if not (t2[0] == "num" and isinstance(t2[1], int)):
            return None
        try:
            t3 = self._pop()
        except PdfParseError:
            return None
        if t3 == ("kw", b"R"):
            return Ref(num, t2[1])
        return None

    # ---------- 间接对象 ----------
    def parse_indirect(self, offset: int) -> IndirectObject:
        """在 offset 处解析 "num gen obj ... endobj"（可含 stream）。"""
        self.pos = offset
        t1 = self._pop()
        t2 = self._pop()
        if t1[0] != "num" or not isinstance(t1[1], int) or \
           t2[0] != "num" or not isinstance(t2[1], int):
            raise PdfParseError("不是合法的对象头", offset)
        num, gen = t1[1], t2[1]
        self._expect_keyword(b"obj")
        value = self.parse_value()
        if isinstance(value, PdfDict):
            nxt = self._peek()
            if nxt == ("kw", b"stream"):
                self._pop()
                value = self._read_stream(value)
        self._expect_keyword(b"endobj")
        return IndirectObject(num=num, gen=gen, value=value, offset=offset)

    def _read_stream(self, dictionary: PdfDict) -> PdfStream:
        # 'stream' 后必须有一个 EOL（CRLF 或 LF），数据从其后开始
        d = self.data
        p = self.pos
        if d[p:p + 2] == b"\r\n":
            p += 2
        elif d[p:p + 1] in (b"\r", b"\n"):
            p += 1
        else:
            raise PdfParseError("stream 关键字后缺少换行", p)
        data_start = p
        length = dictionary.get(Name("/Length"))
        raw = None
        end = None
        if isinstance(length, int) and length >= 0:
            cand = data_start + length
            tail = d[cand:cand + 16]
            if tail[:9] == b"endstream" or tail[1:10] == b"endstream" \
               or tail[2:11] == b"endstream":
                raw = d[data_start:cand]
                end = cand
        if raw is None:
            # /Length 缺失/不可信：回退搜索 endstream
            idx = d.find(b"endstream", data_start)
            if idx < 0:
                raise PdfParseError("找不到 endstream", data_start)
            raw = d[data_start:idx]
            # 去掉 endstream 前的一个 EOL
            if raw.endswith(b"\r\n"):
                raw = raw[:-2]
            elif raw.endswith(b"\n") or raw.endswith(b"\r"):
                raw = raw[:-1]
            end = idx
        # 消费 endstream 及其后的换行，让 endobj 能被正常读到
        self.pos = end
        self._expect_keyword(b"endstream")
        return PdfStream(dictionary, raw)

    # ---------- 工具 ----------
    def keyword_at(self, offset: int, word: bytes) -> bool:
        """判断 offset 处（跳过前导空白后）是否为指定关键字。"""
        self.pos = offset
        try:
            self._skip_ws()
        except Exception:
            return False
        return self.data[self.pos:self.pos + len(word)] == word
