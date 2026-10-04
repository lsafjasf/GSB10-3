# -*- coding: utf-8 -*-
"""顺序扫描重建：不依赖任何索引，从头到尾找出所有间接对象定义。

用于：
- 索引损坏 / startxref 失效 / 文件被截断时的兜底恢复；
- 与 xref 声明做“对拍”，验证索引与实际对象是否一致。

策略：正则定位候选 "num gen obj" 头，再用完整语法解析验证；
验证失败的候选跳过（例如内容流里偶然出现的相似字节）。
同一对象多次定义（增量更新覆盖）时，后者生效，历史保留在 history。
"""

import re

from .objects import ScanRecord
from .parser import Parser, PdfParseError

_HEADER_RE = re.compile(rb"(?<![0-9A-Za-z])(\d{1,10})[ \t]+(\d{1,5})[ \t]+obj(?![0-9A-Za-z])")


def scan_objects(data: bytes):
    """返回 (objects, order, history, errors)。

    objects: {num: ScanRecord}  每个对象最终生效的定义
    order:   [(num, gen, offset)] 按文件中出现的先后
    history: {num: [ScanRecord,...]} 全部定义（含被覆盖的旧版）
    errors:  [(offset, message)] 候选头解析失败的记录
    """
    parser = Parser(data)
    objects = {}
    history = {}
    order = []
    errors = []
    pos = 0
    n = len(data)
    while pos < n:
        m = _HEADER_RE.search(data, pos)
        if not m:
            break
        offset = m.start()
        try:
            obj = parser.parse_indirect(offset)
        except PdfParseError as exc:
            errors.append((offset, str(exc)))
            pos = m.end()
            continue
        rec = ScanRecord(num=obj.num, gen=obj.gen, offset=offset, value=obj.value)
        history.setdefault(obj.num, []).append(rec)
        objects[obj.num] = rec  # 后出现者覆盖（增量更新语义）
        order.append((obj.num, obj.gen, offset))
        # 跳过对象内部，避免误匹配对象体内的相似字节
        pos = max(parser.pos, m.end())
    return objects, order, history, errors
