# -*- coding: utf-8 -*-
"""按顺序展开页面树，并校验每个 /Pages 节点声明的 /Count 与实际子节点数。"""

from .objects import Name, PdfDict, Page, PageNode


def tuple_key(ref):
    return (getattr(ref, "num", -1), getattr(ref, "gen", 0),
            getattr(ref, "stream", -1), getattr(ref, "index", -1))


def node_label(ref, inline):
    return "内联字典" if inline else "%r" % (ref,)


def walk_pages(doc):
    """返回 (pages, nodes, errors)。

    - pages: 按 /Kids 顺序深度优先展开的 /Page 叶子，ordinal 从 1 编号
    - nodes: 访问过的每个节点（含 /Count 声明与实际展开数）
    - errors: /Count 不符、缺 /Kids、循环引用、节点缺失等问题
    """
    pages = []
    nodes = []
    errors = []

    catalog = doc.catalog()
    if catalog is None:
        errors.append("找不到文档目录 (/Root -> /Catalog)，无法展开页面树")
        return pages, nodes, errors
    root_ref = catalog.get(Name("/Pages"))
    if root_ref is None:
        errors.append("目录对象缺少 /Pages")
        return pages, nodes, errors

    visiting = set()

    def visit(ref, inline=False):
        """展开一个节点，返回其子树叶子数；叶子按序追加到 pages。"""
        key = None if inline else tuple_key(ref)
        if key is not None:
            if key in visiting:
                errors.append("页面树存在循环引用: %s" % node_label(ref, inline))
                return 0
            visiting.add(key)
        try:
            d = ref if inline else doc.resolve(ref)
            if not isinstance(d, PdfDict):
                errors.append("页面树节点缺失或不是字典: %s" % node_label(ref, inline))
                return 0
            kind = d.get_name("/Type", "/unknown")
            kids = d.get(Name("/Kids"))
            declared = d.get(Name("/Count"))
            node = PageNode(
                ref=None if inline else ref,
                kind=kind,
                declared_count=declared,
                inline=inline,
            )
            if kind == "/Pages" or isinstance(kids, list):
                node.kids = len(kids) if isinstance(kids, list) else 0
                total = 0
                if isinstance(kids, list):
                    for kid in kids:
                        total += visit(kid, isinstance(kid, PdfDict))
                else:
                    errors.append("/Pages 节点缺少合法 /Kids: %s" % node_label(ref, inline))
                node.actual_count = total
                nodes.append(node)
                if declared is not None and declared != total:
                    errors.append(
                        "/Count 声明 %s 与实际展开页数 %d 不一致 (节点 %s)"
                        % (declared, total, node_label(ref, inline))
                    )
                return total
            # 叶子 /Page
            node.actual_count = 1
            nodes.append(node)
            if inline:
                page = Page(len(pages) + 1, -1, 0, False, -1, -1, d, inline=True)
            else:
                stm = getattr(ref, "stream", -1)
                idx = getattr(ref, "index", -1)
                if stm < 0:
                    # Kids 数组里的普通引用不带压缩位置，从对象表补齐
                    io = doc.objects.get(ref.num)
                    if io is not None and io.stream_num >= 0:
                        stm, idx = io.stream_num, io.stream_index
                page = Page(
                    ordinal=len(pages) + 1,
                    num=ref.num,
                    gen=ref.gen,
                    in_object_stream=stm >= 0,
                    stream_num=stm,
                    stream_index=idx,
                    node=d,
                )
            pages.append(page)
            return 1
        finally:
            if key is not None:
                visiting.discard(key)

    visit(root_ref)
    for i, p in enumerate(pages, 1):
        p.ordinal = i
    return pages, nodes, errors
