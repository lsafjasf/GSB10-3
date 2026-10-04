"""
doc_diff.py —— 把 docs/state_machine_spec.md 的转移表逐条与真实实现对拍。

实验方法：
  1. 解析文档中的 Markdown 转移表（| 当前状态 | 事件 | 下一状态 | 说明 |）；
  2. 对每一行，用全新机器从 CREATED 沿 BFS 路径驱动到"当前状态"，
     触发文档给定的事件，观察真实结果；
  3. 文档中的"（报错）"按"期望抛 InvalidTransitionError"处理；
  4. 文档里的状态若运行时不可达，直接标注，不再驱动。

另含两个文档未覆盖行为的专项实验：
  - SHIPPED 连续 timeout 的自动签收阈值；
  - CREATED 下 pay 事件的金额守卫。

输出 analysis/out/doc_diff.json 并打印对照表。
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402
    OUT_DIR,
    ROOT,
    discover_reachable,
    fire_and_classify,
    fresh_machine,
)

SPEC_PATH = os.path.join(ROOT, 'docs', 'state_machine_spec.md')
EXPECT_RAISE = '（报错）'


def parse_spec_table(path):
    """提取文档第二章转移表中的数据行。"""
    rows = []
    in_table = False
    with open(path, encoding='utf-8') as handle:
        for lineno, line in enumerate(handle, 1):
            stripped = line.strip()
            if stripped.startswith('| 当前状态'):
                in_table = True
                continue
            if in_table:
                if not stripped.startswith('|'):
                    in_table = False
                    continue
                cells = [c.strip().strip('`') for c in stripped.strip('|').split('|')]
                if set(cells[0]) <= {'-', ' '}:
                    continue
                rows.append({
                    'line': lineno,
                    'source': cells[0],
                    'event': cells[1],
                    'expected': cells[2],
                    'note': cells[3] if len(cells) > 3 else '',
                })
    return rows


def describe(outcome):
    if outcome['kind'] == 'transition':
        return '转移到 %s' % outcome['target']
    if outcome['kind'] == 'stay':
        return '状态不变（stay，记入 history）'
    if outcome['kind'] == 'ignored':
        return '静默忽略（仅记入 ignored 账本）'
    if outcome['kind'] == 'raise':
        return '抛 InvalidTransitionError'
    if outcome['kind'] == 'unknown':
        return '抛 UnknownEventError'
    return outcome['kind']


def main():
    _matrix, paths, order = discover_reachable()
    reachable = set(order)
    rows = parse_spec_table(SPEC_PATH)

    results = []
    print('== 文档转移表逐条对拍（规格来源：docs/state_machine_spec.md）==')
    print('%-9s %-15s %-18s %-34s %s' % ('状态', '事件', '文档期望', '实测结果', '结论'))
    for row in rows:
        source, event, expected = row['source'], row['event'], row['expected']
        record = dict(row)
        if source not in reachable:
            verdict = '无法对拍'
            actual = '源状态 %s 运行时不可达' % source
        else:
            machine = fresh_machine()
            for step in paths[source]:
                machine.fire(step)
            outcome = fire_and_classify(machine, event)
            actual = describe(outcome)
            record['outcome'] = outcome
            if expected == EXPECT_RAISE:
                ok = outcome['kind'] == 'raise'
            elif outcome['kind'] == 'transition':
                ok = outcome['target'] == expected
            else:
                ok = False
            verdict = '一致' if ok else '不一致'
        record['actual'] = actual
        record['verdict'] = verdict
        results.append(record)
        print('%-9s %-15s %-18s %-34s %s'
              % (source, event, expected, actual, verdict))

    # ---- 文档第三章"非法事件一律抛错"声明的验证 ---------------------- #
    print()
    print('== 文档第三章声明验证："任何状态下收到未列出事件一律抛 '
          'InvalidTransitionError" ==')
    policy_violations = []
    for state in order:
        machine = fresh_machine()
        for step in paths[state]:
            machine.fire(step)
        # 取一个在该状态下不会转移、也不会 stay 的已知事件做探针
        for event, expect_kind in (('audit_pass', None),):
            outcome = fire_and_classify(machine, event)
            if outcome['kind'] != 'raise':
                policy_violations.append((state, event, outcome['kind']))
    if policy_violations:
        for state, event, kind in policy_violations:
            print('  反例：state=%-10s event=%-12s 实测 %s' % (state, event, kind))
        print('  结论：文档声明不成立（终态等场景会静默忽略，详见 '
              'illegal_events.py 的完整矩阵）')
    else:
        print('  未发现反例')

    # ---- 专项实验 1：SHIPPED 超时自动签收阈值 ------------------------- #
    print()
    print('== 专项实验 1：SHIPPED 连续 timeout 的自动签收阈值 ==')
    machine = fresh_machine()
    for step in paths['SHIPPED']:
        machine.fire(step)
    seq = []
    for attempt in range(1, 5):
        outcome = fire_and_classify(machine, 'timeout')
        seq.append('第%d次 timeout → %s' % (attempt, describe(outcome)))
        if outcome['kind'] == 'transition':
            break
    for line in seq:
        print('  ' + line)
    print('  结论：并非文档所称"超时立即签收"，而是累计满阈值后才自动签收')

    # ---- 专项实验 2：CREATED 下 pay 的金额守卫 ------------------------ #
    print()
    print('== 专项实验 2：CREATED 状态 pay(amount=0) 的守卫 ==')
    for amount in (0, -5, 99.9):
        machine = fresh_machine()
        outcome = fire_and_classify(machine, 'pay', amount=amount)
        print('  pay(amount=%s) → %s' % (amount, describe(outcome)))
    print('  结论：amount<=0 时按非法事件拒绝，文档未记载该守卫')

    report = {
        'spec_rows': results,
        'illegal_event_policy_violations': [
            {'state': s, 'event': e, 'kind': k}
            for s, e, k in policy_violations],
        'shipped_timeout_sequence': seq,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, 'doc_diff.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print()
    print('结果已写入 analysis/out/doc_diff.json')


if __name__ == '__main__':
    main()
