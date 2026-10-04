"""依赖有向图：节点是 (sheet, row, col)，边 A -> B 表示 A 依赖 B（B 要先算）。

- 用 Tarjan 强连通分量检测循环引用，环上的全部单元格都会列出。
- 用 Kahn 算法按依赖关系给出拓扑求值顺序。
"""

from collections import defaultdict, deque

from .parser import parse_formula, collect_refs


class DependencyGraph:
    def __init__(self):
        self.edges = defaultdict(set)   # node -> 它依赖的节点集合
        self.formulas = {}              # node -> 公式 AST

    def add_formula(self, node, formula_text, current_sheet):
        ast = parse_formula(formula_text)
        self.formulas[node] = ast
        deps, _ = collect_refs(ast, current_sheet)
        self.edges[node] = set(deps)

    def dependencies(self, node):
        return set(self.edges.get(node, ()))

    def dependents(self, node):
        return {src for src, dsts in self.edges.items() if node in dsts}

    # ---- 循环检测：Tarjan SCC，大小 >1 或带自环的 SCC 即循环 ----
    def strongly_connected_components(self):
        index_of = {}
        lowlink = {}
        on_stack = set()
        stack = []
        sccs = []
        counter = [0]

        def strongconnect(v):
            # 迭代版 Tarjan，避免深依赖链触发递归上限
            work = [(v, iter(sorted(self.edges.get(v, ()))))]
            index_of[v] = lowlink[v] = counter[0]
            counter[0] += 1
            stack.append(v)
            on_stack.add(v)
            while work:
                node, it = work[-1]
                advanced = False
                for w in it:
                    if w not in index_of:
                        index_of[w] = lowlink[w] = counter[0]
                        counter[0] += 1
                        stack.append(w)
                        on_stack.add(w)
                        work.append((w, iter(sorted(self.edges.get(w, ())))))
                        advanced = True
                        break
                    elif w in on_stack:
                        lowlink[node] = min(lowlink[node], index_of[w])
                if advanced:
                    continue
                work.pop()
                if work:
                    parent = work[-1][0]
                    lowlink[parent] = min(lowlink[parent], lowlink[node])
                if lowlink[node] == index_of[node]:
                    scc = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        scc.append(w)
                        if w == node:
                            break
                    sccs.append(scc)

        for v in sorted(self.edges):
            if v not in index_of:
                strongconnect(v)
        return sccs

    def cycles(self):
        """返回所有循环（每个循环是节点列表，含自引用与互相引用）。"""
        result = []
        for scc in self.strongly_connected_components():
            if len(scc) > 1:
                result.append(sorted(scc))
            elif scc[0] in self.edges.get(scc[0], ()):
                result.append(sorted(scc))  # 自引用
        return result

    def cyclic_nodes(self):
        nodes = set()
        for cyc in self.cycles():
            nodes.update(cyc)
        return nodes

    # ---- 拓扑排序（Kahn）：被依赖者排前面 ----
    def evaluation_order(self, only=None):
        """返回拓扑序节点列表。环上的节点不在结果中（用 cycles() 单独取）。

        only: 若给定节点集合，只对该子图排序。
        """
        nodes = set(self.edges)
        for dsts in self.edges.values():
            nodes.update(dsts)
        if only is not None:
            nodes &= set(only)
        cyclic = self.cyclic_nodes()
        nodes -= cyclic

        indeg = {n: 0 for n in nodes}
        rdeps = defaultdict(set)  # 被依赖者 -> 依赖它的节点
        for src in nodes:
            for dst in self.edges.get(src, ()):
                if dst in nodes:
                    indeg[src] += 1
                    rdeps[dst].add(src)
        ready = deque(sorted(n for n in nodes if indeg[n] == 0))
        order = []
        while ready:
            n = ready.popleft()
            order.append(n)
            for m in sorted(rdeps.get(n, ())):
                indeg[m] -= 1
                if indeg[m] == 0:
                    ready.append(m)
        return order


def build_dependency_graph(workbook):
    """从 Workbook 构建依赖图。返回 DependencyGraph。"""
    g = DependencyGraph()
    for sname, row, col, formula in workbook.formula_cells():
        g.add_formula((sname, row, col), formula, sname)
    return g
