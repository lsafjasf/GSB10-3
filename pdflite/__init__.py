# -*- coding: utf-8 -*-
"""pdflite：零依赖 PDF 对象定位与解析（传统偏移表 + 流式索引）。"""

from .document import Document
from .objects import Name, Ref, PdfDict, PdfStream, IndirectObject, Page, PageNode
from .parser import Parser, PdfParseError
from .scanner import scan_objects
from .xref import find_startxref, load_xref_chain
from .pages import walk_pages
from . import verify

__all__ = [
    "Document", "Name", "Ref", "PdfDict", "PdfStream", "IndirectObject",
    "Page", "PageNode", "Parser", "PdfParseError", "scan_objects",
    "find_startxref", "load_xref_chain", "walk_pages", "verify",
]

__version__ = "0.1.0"
