# -*- coding: utf-8 -*-
"""对拍：把 xref 索引声明与顺序扫描重建结果逐条比对。

比对维度：
- 未压缩对象：声明偏移 vs 扫描到的实际偏移（含被覆盖的历史版本）
- 压缩对象：声明的 (对象流号, 流内序号) vs 对象流头部实际映射
- 集合差异：索引声明但扫描不到、扫描到但索引未声明
"""

from dataclasses import dataclass, field

from .objects import PdfStream


@dataclass
class Row:
    num: int
    status: str      # match / repaired / rebuilt / dangling / extra / mismatch
    declared: str
    actual: str
    note: str = ""


@dataclass
class Report:
    rows: list = field(default_factory=list)
    ok: bool = True
    notes: list = field(default_factory=list)

    def add(self, row):
        self.rows.append(row)
        if row.status in ("dangling", "mismatch"):
            self.ok = False

    def counts(self):
        c = {}
        for r in self.rows:
            c[r.status] = c.get(r.status, 0) + 1
        return c


def _object_stream_pairs(doc, stm_num):
    """对象流头部声明的 (流内序号 -> 全局编号) 映射。"""
    holder = doc.objects.get(stm_num)
    if holder is None or not isinstance(holder.value, PdfStream):
        return None
    d = holder.value.dict
    n = d.get("/N")
    first = d.get("/First")
    if not isinstance(n, int) or not isinstance(first, int):
        return None
    from .filters import decode_stream, FilterError
    try:
        data = decode_stream(holder.value)
    except FilterError:
        return None
    parts = data[:first].split()
    if len(parts) < 2 * n:
        return None
    out = {}
    for i in range(n):
        out[i] = int(parts[2 * i])
    return out


def compare(doc) -> Report:
    rep = Report()
    declared = {}   # num -> 最新生效条目
    for section in doc.sections:
        for num, entry in section.entries.items():
            declared.setdefault(num, entry)

    all_nums = sorted(set(declared) | set(doc.scan_map))
    for num in all_nums:
        entry = declared.get(num)
        rec = doc.scan_map.get(num)
        if entry is not None and entry.kind == 1:
            continue  # 空闲条目无需对拍
        if entry is None:
            rep.add(Row(num, "extra", "-",
                        "偏移 %d" % rec.offset,
                        "索引未声明，扫描发现"))
            continue
        if rec is None and entry.kind == 0:
            rep.add(Row(num, "dangling", "偏移 %d" % entry.field2, "-",
                        "索引声明但文件中找不到对象"))
            continue
        if entry.kind == 0:
            if entry.field2 == rec.offset:
                rep.add(Row(num, "match", "偏移 %d" % entry.field2,
                            "偏移 %d" % rec.offset))
            else:
                history = doc.scan_history.get(num, [])
                if any(h.offset == entry.field2 for h in history):
                    rep.add(Row(num, "match", "偏移 %d" % entry.field2,
                                "偏移 %d" % rec.offset,
                                "指向历史版本（增量更新）"))
                else:
                    rep.add(Row(num, "repaired", "偏移 %d" % entry.field2,
                                "偏移 %d" % rec.offset,
                                "声明偏移失效，已按扫描重建"))
        elif entry.kind == 2:
            stm, idx = entry.field2, entry.gen
            pairs = _object_stream_pairs(doc, stm)
            if pairs is None:
                rep.add(Row(num, "mismatch", "流 %d 序号 %d" % (stm, idx),
                            "-", "对象流不可用"))
            elif pairs.get(idx) == num:
                rep.add(Row(num, "match", "流 %d 序号 %d" % (stm, idx),
                            "流 %d 序号 %d" % (stm, idx)))
            else:
                rep.add(Row(num, "mismatch", "流 %d 序号 %d" % (stm, idx),
                            "流内序号 %d 实际编号 %s"
                            % (idx, pairs.get(idx, "越界")),
                            "对象流映射与声明不符"))
    if not doc.index_ok:
        rep.notes.append("索引不可用，对象表完全由顺序扫描重建")
    return rep


def format_report(rep: Report) -> str:
    lines = []
    lines.append("%-6s %-9s %-22s %-22s %s" % ("对象", "状态", "索引声明", "扫描重建", "备注"))
    lines.append("-" * 78)
    status_cn = {
        "match": "一致", "repaired": "已修复", "rebuilt": "重建",
        "dangling": "悬空", "extra": "多余", "mismatch": "不符",
    }
    for r in rep.rows:
        lines.append("%-6d %-9s %-22s %-22s %s" % (
            r.num, status_cn.get(r.status, r.status), r.declared, r.actual, r.note))
    c = rep.counts()
    summary = "，".join("%s %d" % (status_cn.get(k, k), v) for k, v in sorted(c.items()))
    lines.append("-" * 78)
    lines.append("合计：%s" % (summary or "无条目"))
    for note in rep.notes:
        lines.append("说明：%s" % note)
    lines.append("结论：%s" % ("对拍通过" if rep.ok else "对拍发现不一致"))
    return "\n".join(lines)
