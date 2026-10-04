# -*- coding: utf-8 -*-
"""文档装配：索引加载、顺序扫描重建、对拍、对象流解压、对象定位。"""

from .objects import Name, Ref, PdfDict, PdfStream, IndirectObject, XRefEntry
from .parser import Parser, PdfParseError
from .filters import decode_stream, FilterError
from .xref import find_startxref, load_xref_chain
from .scanner import scan_objects
from .pages import walk_pages


class Document:
    """加载后的 PDF 文档视图。"""

    def __init__(self, data: bytes, path: str = "<bytes>"):
        self.data = data
        self.path = path
        self.parser = Parser(data)
        self.version = None
        self.sections = []          # xref 段，最新在前
        self.trailer = PdfDict()
        self.index_mode = "none"    # table / stream / mixed / none
        self.index_ok = False
        self.recovered = False      # 是否回退到顺序扫描重建
        self.warnings = []
        self.scan_map = {}
        self.scan_order = []
        self.scan_history = {}
        self.scan_errors = []
        self.objects = {}           # num -> IndirectObject（最终生效定义）
        self.compressed = {}        # num -> (stream_num, index)
        self._objstm_cache = {}     # stream_num -> [成员值...]
        self._load()

    # ---------------- 加载流程 ----------------
    @classmethod
    def load(cls, path: str):
        with open(path, "rb") as fh:
            return cls(fh.read(), path)

    def _load(self):
        data = self.data
        if data[:5] == b"%PDF-":
            nl = data.find(b"\n", 0, 64)
            head = data[5:nl if nl > 0 else 64]
            self.version = head.strip().decode("latin-1", "replace")
        else:
            self.warnings.append("缺少 %PDF- 文件头")

        # 1) 顺序扫描（重建基线，同时用于对拍与兜底）
        (self.scan_map, self.scan_order,
         self.scan_history, self.scan_errors) = scan_objects(data)

        # 2) 尝试按索引加载
        start = find_startxref(data)
        if start is None:
            self.warnings.append("找不到 startxref，回退到顺序扫描重建")
        else:
            try:
                self.sections, self.trailer = load_xref_chain(data, start)
                self.index_ok = True
                kinds = {"stream" if s.is_stream else "table" for s in self.sections}
                self.index_mode = kinds.pop() if len(kinds) == 1 else "mixed"
            except PdfParseError as exc:
                self.warnings.append(
                    "索引解析失败 (%s)，回退到顺序扫描重建" % exc)

        # 3) 组装对象表
        self._build_objects()

        # 4) 对象流解压与映射
        self._build_compressed()

    # ---------------- 对象表组装 ----------------
    def _build_objects(self):
        declared = {}
        if self.index_ok:
            for section in self.sections:  # 最新在前，先到先得
                for num, entry in section.entries.items():
                    declared.setdefault(num, entry)

        for num, entry in declared.items():
            if entry.kind == 1:
                continue  # 空闲条目
            if entry.kind == 2:
                continue  # 压缩对象在 _build_compressed 处理
            offset = entry.field2
            obj = self._parse_at(offset)
            if obj is not None and obj.num == num:
                obj.section_offset = entry.section_offset
                obj.header_ok = (obj.gen == entry.gen)
                if not obj.header_ok:
                    self.warnings.append(
                        "对象 %d 声明代 %d 与实际代 %d 不一致" % (num, entry.gen, obj.gen))
                self.objects[num] = obj
                continue
            # 声明偏移失效：用扫描结果修复
            actual = "解析失败" if obj is None else "编号为 %d" % obj.num
            self.warnings.append(
                "对象 %d 的声明偏移 %d 失效（实际%s），已用扫描结果修复"
                % (num, offset, actual))
            rec = self.scan_map.get(num)
            if rec is not None:
                self.objects[num] = IndirectObject(
                    num=num, gen=rec.gen, value=rec.value, offset=rec.offset,
                    section_offset=entry.section_offset, header_ok=False)
            else:
                self.warnings.append("对象 %d 在文件中找不到定义（悬空条目）" % num)

        # 索引未声明但扫描发现的对象（截断/损坏场景）
        for num, rec in self.scan_map.items():
            if num not in self.objects and num not in declared:
                self.objects[num] = IndirectObject(
                    num=num, gen=rec.gen, value=rec.value, offset=rec.offset)
                if self.index_ok:
                    self.warnings.append("对象 %d 未被索引声明，由扫描补充" % num)

        if not self.index_ok:
            self.recovered = True
            # 无索引时仍可从扫描到的 XRef 流中恢复压缩对象映射
            self._recover_xref_stream_from_scan()

    def _recover_xref_stream_from_scan(self):
        """截断等场景下，从扫描到的 /XRef 流对象中提取压缩对象映射。"""
        best = None
        for num, rec in self.scan_map.items():
            v = rec.value
            if isinstance(v, PdfStream) and v.dict.get_name("/Type") == "/XRef":
                best = rec  # 扫描顺序即文件顺序，后者覆盖（最新）
        if best is None:
            return
        try:
            from .xref import _parse_stream_section
            section = _parse_stream_section(self.data, best.offset, self.parser)
        except PdfParseError as exc:
            self.warnings.append("扫描到的 XRef 流解析失败: %s" % exc)
            return
        self.sections = [section]
        for k, v in section.trailer.items():
            self.trailer.setdefault(k, v)
        self.index_mode = "stream"
        self.warnings.append(
            "从扫描到的 XRef 流(对象 %d)恢复了索引映射" % best.num)

    def _parse_at(self, offset):
        if offset < 0 or offset >= len(self.data):
            return None
        try:
            return self.parser.parse_indirect(offset)
        except PdfParseError:
            return None

    # ---------------- 对象流 ----------------
    def _build_compressed(self):
        # 汇总所有段中的压缩条目（最新段优先）
        for section in self.sections:
            for num, entry in section.entries.items():
                if entry.kind == 2 and num not in self.compressed:
                    self.compressed[num] = (entry.field2, entry.gen)

        for num, (stm_num, index) in sorted(self.compressed.items()):
            members = self._object_stream_members(stm_num)
            if members is None:
                self.warnings.append(
                    "压缩对象 %d 所在对象流 %d 不可用" % (num, stm_num))
                continue
            if not (0 <= index < len(members)):
                self.warnings.append(
                    "压缩对象 %d 的流内序号 %d 越界(对象流 %d 共 %d 个)"
                    % (num, index, stm_num, len(members)))
                continue
            self.objects[num] = IndirectObject(
                num=num, gen=0, value=members[index],
                stream_num=stm_num, stream_index=index)

    def _object_stream_members(self, stm_num):
        """解析对象流，返回成员值列表（顺序即流内序号）；失败返回 None。"""
        if stm_num in self._objstm_cache:
            return self._objstm_cache[stm_num]
        result = None
        holder = self.objects.get(stm_num)
        if holder is not None and isinstance(holder.value, PdfStream):
            stream = holder.value
            if stream.dict.get_name("/Type") == "/ObjStm":
                try:
                    result = self._parse_object_stream(stream)
                except (PdfParseError, FilterError) as exc:
                    self.warnings.append("对象流 %d 解析失败: %s" % (stm_num, exc))
        self._objstm_cache[stm_num] = result
        return result

    def _parse_object_stream(self, stream: PdfStream):
        d = stream.dict
        n = d.get(Name("/N"))
        first = d.get(Name("/First"))
        if not isinstance(n, int) or not isinstance(first, int):
            raise PdfParseError("对象流缺少 /N 或 /First")
        data = decode_stream(stream)
        header = data[:first]
        parts = header.split()
        if len(parts) < 2 * n:
            raise PdfParseError("对象流头部编号/偏移对不足")
        pairs = []
        for i in range(n):
            pairs.append((int(parts[2 * i]), int(parts[2 * i + 1])))
        members = []
        for i, (num, off) in enumerate(pairs):
            start = first + off
            end = first + pairs[i + 1][1] if i + 1 < n else len(data)
            sub = Parser(data[start:end], 0)
            members.append(sub.parse_value())
        return members

    # ---------------- 对外接口 ----------------
    def resolve(self, ref):
        """按 Ref 解析对象值；Ref 可携带压缩位置信息。"""
        if isinstance(ref, PdfDict):
            return ref
        if not isinstance(ref, Ref):
            return None
        if ref.is_compressed():
            members = self._object_stream_members(ref.stream)
            if members is not None and 0 <= ref.index < len(members):
                return members[ref.index]
            return None
        obj = self.objects.get(ref.num)
        return obj.value if obj is not None else None

    def get_object(self, num):
        return self.objects.get(num)

    def root_num(self):
        root = self.trailer.get(Name("/Root"))
        return root.num if isinstance(root, Ref) else None

    def catalog_num(self):
        """目录对象编号；trailer 缺失时从扫描结果中识别。"""
        root = self.trailer.get(Name("/Root"))
        if isinstance(root, Ref):
            return root.num
        for num, rec in self.scan_map.items():
            if isinstance(rec.value, PdfDict) and rec.value.get_name("/Type") == "/Catalog":
                return num
        return None

    def catalog(self):
        root = self.trailer.get(Name("/Root"))
        if isinstance(root, Ref):
            d = self.resolve(root)
            if isinstance(d, PdfDict):
                return d
        # 索引缺失时回退：从扫描结果里找 /Type /Catalog
        for num, rec in self.scan_map.items():
            v = rec.value
            if isinstance(v, PdfDict) and v.get_name("/Type") == "/Catalog":
                self.warnings.append(
                    "trailer 缺少 /Root，使用扫描到的 /Catalog 对象 %d" % num)
                return v
        return None

    def compressed_pairs(self):
        """对象流映射：{对象流号: [(流内序号, 全局编号), ...]}，按流内序号排序。"""
        pairs = {}
        for num, (stm, idx) in self.compressed.items():
            pairs.setdefault(stm, []).append((idx, num))
        for stm in pairs:
            pairs[stm].sort()
        return pairs

    def pages(self):
        return walk_pages(self)

    def overridden(self):
        """被增量更新覆盖的对象编号列表。"""
        return sorted(n for n, h in self.scan_history.items() if len(h) > 1)
