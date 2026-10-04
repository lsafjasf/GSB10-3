"""
static_scan.py —— 用标准库 ast 静态解析被测状态机。

输出 analysis/out/static.json：
  - KNOWN_EVENTS / TERMINAL_STATES / MAX_SHIP_TIMEOUTS
  - fire() 内每个状态区块（行号）、每个事件分支（行号、目标、守卫表达式）
  - 守卫恒假 / 事件未注册 的死分支
  - 静态可达性（从 CREATED 出发，只走活分支）→ 不可达状态与转移
  - 终态拦截、未知事件闸门、防御性兜底的行号
"""

import ast
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import OUT_DIR, ROOT  # noqa: E402

MODULE_PATH = os.path.join(ROOT, 'legacy', 'legacy_order_machine.py')

EVENT_GATES = {'_go': 'transition', '_stay': 'stay',
               '_ignore': 'ignored', '_illegal': 'raise'}


def literal_const(node, module_consts, init_consts):
    """对简单常量表达式做定值求值；遇到未知符号返回 None。"""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return module_consts.get(node.id)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
            and node.value.id == 'self':
        return init_consts.get(node.attr)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = literal_const(node.operand, module_consts, init_consts)
        return -inner if isinstance(inner, (int, float)) else None
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        left = literal_const(node.left, module_consts, init_consts)
        right = literal_const(node.comparators[0], module_consts, init_consts)
        if left is None or right is None:
            return None
        op = node.ops[0]
        if isinstance(op, ast.Eq):
            return left == right
        if isinstance(op, ast.NotEq):
            return left != right
        if isinstance(op, ast.Gt):
            return left > right
        if isinstance(op, ast.GtE):
            return left >= right
        if isinstance(op, ast.Lt):
            return left < right
        if isinstance(op, ast.LtE):
            return left <= right
    return None


def is_event_compare(node):
    """test 是否形如 event == 'xxx'（可能是 And 的第一个操作数）。"""
    if isinstance(node, ast.Compare) and len(node.ops) == 1 \
            and isinstance(node.ops[0], ast.Eq) \
            and isinstance(node.left, ast.Name) and node.left.id == 'event' \
            and isinstance(node.comparators[0], ast.Constant) \
            and isinstance(node.comparators[0].value, str):
        return node.comparators[0].value
    return None


def split_event_test(test):
    """从 If.test 中拆出 (event_name, 剩余守卫 ast 或 None)。"""
    event_name = is_event_compare(test)
    if event_name is not None:
        return event_name, None
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        first = is_event_compare(test.values[0])
        if first is not None:
            rest = test.values[1] if len(test.values) == 2 \
                else ast.BoolOp(ast.And(), test.values[1:])
            return first, rest
    return None, None


class FireVisitor(ast.NodeVisitor):
    def __init__(self, module_consts, init_consts, known_events):
        self.module_consts = module_consts
        self.init_consts = init_consts
        self.known_events = set(known_events)
        self.sections = {}
        self.unknown_gate_line = None
        self.terminal_gate_line = None
        self.defensive_raise_line = None

    # -- 带条件栈收集 _go/_stay/_ignore/_illegal 调用与 raise ---------- #
    def collect_outcomes(self, nodes, conditions, acc):
        for node in nodes:
            if isinstance(node, ast.If):
                event_name, _guard = split_event_test(node.test)
                if event_name is not None:
                    # 事件分支边界：条件由调用方单独处理，不再往里压栈
                    self.collect_outcomes(node.body, conditions, acc)
                    self.collect_outcomes(node.orelse, conditions, acc)
                else:
                    text = ast.unparse(node.test)
                    self.collect_outcomes(
                        node.body, conditions + [(text, True)], acc)
                    self.collect_outcomes(
                        node.orelse, conditions + [(text, False)], acc)
            elif isinstance(node, ast.Return) and isinstance(node.value, ast.Call):
                call = node.value
                if isinstance(call.func, ast.Attribute) \
                        and call.func.attr in EVENT_GATES:
                    acc.append(self._outcome(call, conditions))
            else:
                self.generic_visit_with_conditions(node, conditions, acc)

    def generic_visit_with_conditions(self, node, conditions, acc):
        for child in ast.iter_child_nodes(node):
            self.collect_outcomes([child], conditions, acc)

    def _outcome(self, call, conditions):
        kind = EVENT_GATES[call.func.attr]
        item = {'kind': kind, 'line': call.lineno,
                'conditions': [{'test': t, 'arm': arm} for t, arm in conditions]}
        if kind == 'transition':
            target = call.args[0]
            item['target'] = target.value if isinstance(target, ast.Constant) else None
        return item

    def visit_FunctionDef(self, node):
        if node.name != 'fire':
            return
        statements = list(node.body)
        idx = 0
        while idx < len(statements) and not isinstance(statements[idx], ast.If):
            idx += 1
        for stmt in statements[idx:]:
            if isinstance(stmt, ast.Raise):
                self.defensive_raise_line = stmt.lineno
                continue
            if not isinstance(stmt, ast.If):
                continue
            # 顶层闸门判断
            text = ast.unparse(stmt.test)
            if self.unknown_gate_line is None and 'KNOWN_EVENTS' in text:
                self.unknown_gate_line = stmt.lineno
                continue
            if self.terminal_gate_line is None and 'TERMINAL_STATES' in text:
                self.terminal_gate_line = stmt.lineno
                continue
            state_name = self._state_of(stmt.test)
            if state_name is None:
                continue
            section = {'state': state_name, 'line': stmt.lineno, 'branches': [],
                       'fallback': None}
            self._scan_section_body(stmt.body, section)
            self.sections[state_name] = section

    def _state_of(self, test):
        if isinstance(test, ast.Compare) and len(test.ops) == 1 \
                and isinstance(test.ops[0], ast.Eq):
            left, right = test.left, test.comparators[0]
            if isinstance(left, ast.Attribute) and left.attr == 'state' \
                    and isinstance(right, ast.Constant):
                return right.value
        return None

    def _scan_section_body(self, body, section):
        for stmt in body:
            if isinstance(stmt, ast.If):
                event_name, guard = split_event_test(stmt.test)
                if event_name is None:
                    # 分支内部的条件（如 timeout 计数），继续下钻
                    outcomes = []
                    self.collect_outcomes([stmt], [], outcomes)
                    continue
                outcomes = []
                self.collect_outcomes(stmt.body, [], outcomes)
                guard_text = ast.unparse(guard) if guard is not None else None
                guard_value = literal_const(
                    guard, self.module_consts, self.init_consts) \
                    if guard is not None else True
                if event_name not in self.known_events:
                    dead_reason = 'event-not-registered'
                elif guard_value is False:
                    dead_reason = 'guard-statically-false'
                else:
                    dead_reason = None
                section['branches'].append({
                    'event': event_name,
                    'line': stmt.lineno,
                    'guard': guard_text,
                    'guard_value_literal': guard_value,
                    'dead_reason': dead_reason,
                    'outcomes': outcomes,
                })
            elif isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Call) \
                    and isinstance(stmt.value.func, ast.Attribute) \
                    and stmt.value.func.attr == '_illegal':
                section['fallback'] = {
                    'kind': 'raise', 'line': stmt.lineno}
            elif isinstance(stmt, ast.Raise):
                section['fallback'] = {
                    'kind': 'raise', 'line': stmt.lineno}


def evaluate_module(path):
    with open(path, encoding='utf-8') as handle:
        tree = ast.parse(handle.read(), filename=path)

    module_consts = {}
    init_consts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    module_consts[target.id] = value
        if isinstance(node, ast.ClassDef) and node.name == 'LegacyOrderMachine':
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == '__init__':
                    for stmt in item.body:
                        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                            target = stmt.targets[0]
                            if isinstance(target, ast.Attribute) \
                                    and isinstance(target.value, ast.Name) \
                                    and target.value.id == 'self':
                                try:
                                    init_consts[target.attr] = \
                                        ast.literal_eval(stmt.value)
                                except (ValueError, SyntaxError):
                                    pass

    known_events = module_consts['KNOWN_EVENTS']
    visitor = FireVisitor(module_consts, init_consts, known_events)
    visitor.visit(tree)

    # ---- 静态可达性：只沿活分支的 _go 边做 BFS --------------------- #
    edges = []
    for state, section in visitor.sections.items():
        for branch in section['branches']:
            if branch['dead_reason'] is not None:
                continue
            for outcome in branch['outcomes']:
                if outcome['kind'] == 'transition' and outcome['target']:
                    edges.append((state, branch['event'], outcome['target'],
                                  branch['line']))
    reachable = {'CREATED'}
    changed = True
    while changed:
        changed = False
        for source, _event, target, _line in edges:
            if source in reachable and target not in reachable:
                reachable.add(target)
                changed = True

    for state, section in visitor.sections.items():
        section['statically_reachable'] = state in reachable

    return {
        'module_path': os.path.relpath(path, ROOT),
        'constants': {
            'KNOWN_EVENTS': module_consts.get('KNOWN_EVENTS'),
            'TERMINAL_STATES': module_consts.get('TERMINAL_STATES'),
            'ALL_STATES': module_consts.get('ALL_STATES'),
            'MAX_SHIP_TIMEOUTS': module_consts.get('MAX_SHIP_TIMEOUTS'),
            'init_constants': init_consts,
        },
        'gates': {
            'unknown_event_gate_line': visitor.unknown_gate_line,
            'terminal_state_gate_line': visitor.terminal_gate_line,
            'defensive_raise_line': visitor.defensive_raise_line,
        },
        'sections': list(visitor.sections.values()),
        'static_reachable_states': sorted(reachable),
        'static_edges': [
            {'source': s, 'event': e, 'target': t, 'line': ln}
            for s, e, t, ln in edges
        ],
    }


def main():
    report = evaluate_module(MODULE_PATH)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, 'static.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print('== 静态扫描：%s ==' % os.path.relpath(MODULE_PATH, ROOT))
    print('状态区块 %d 个；静态可达状态：%s'
          % (len(report['sections']), ', '.join(report['static_reachable_states'])))
    print()
    print('%-12s %-16s %-6s %-22s %s' % ('STATE', 'EVENT', 'LINE', 'OUTCOME', 'NOTE'))
    for section in report['sections']:
        state = section['state']
        reach_flag = '' if section['statically_reachable'] else '  [状态不可达]'
        for branch in section['branches']:
            outcomes = ';'.join(
                ('->%s' % o['target']) if o['kind'] == 'transition'
                else o['kind'] for o in branch['outcomes'])
            note = branch['dead_reason'] or ''
            note += reach_flag if note == '' and reach_flag else reach_flag
            print('%-12s %-16s %-6d %-22s %s'
                  % (state, branch['event'], branch['line'], outcomes, note))
        if section['fallback']:
            note = 'fallback ' + reach_flag.strip()
            print('%-12s %-16s %-6d %-22s %s'
                  % (state, '(else)', section['fallback']['line'],
                     section['fallback']['kind'], note))
    print()
    print('闸门：未知事件 L%(unknown_event_gate_line)s，终态拦截 '
          'L%(terminal_state_line)s，防御性兜底 L%(defensive)s'
          % {'unknown_event_gate_line': report['gates']['unknown_event_gate_line'],
             'terminal_state_line': report['gates']['terminal_state_gate_line'],
             'defensive': report['gates']['defensive_raise_line']})
    print('结果已写入 analysis/out/static.json')


if __name__ == '__main__':
    main()
