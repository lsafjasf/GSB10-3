"""
illegal_events.py —— 逐项回答："非法事件在某状态下是抛错还是被静默忽略？"

覆盖三个层次：
  A. 未注册事件（不在 KNOWN_EVENTS 中）× 每个状态；
  B. 已注册但该状态不接受的事件 × 每个可达状态（区分抛错 / 静默忽略 /
     正常自循环 stay）；
  C. 不可达状态（AUDITING）内的非法事件行为（白盒探针）。

输出 analysis/out/illegal_events.json 并打印逐项结论。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402
    BOGUS_EVENT,
    KNOWN_EVENTS,
    OUT_DIR,
    discover_reachable,
    fire_and_classify,
    fresh_machine,
    poke_state,
)

LABEL = {
    'transition': '转移（合法）',
    'stay': '自循环 stay（合法，记 history）',
    'ignored': '静默忽略',
    'raise': '抛 InvalidTransitionError',
    'unknown': '抛 UnknownEventError',
}


def main():
    matrix, paths, order = discover_reachable()

    print('== A. 未注册事件 %r × 各状态 ==' % BOGUS_EVENT)
    unknown_layer = {}
    for state in order + ['AUDITING']:
        machine = poke_state(state) if state != 'CREATED' else fresh_machine()
        outcome = fire_and_classify(machine, BOGUS_EVENT)
        unknown_layer[state] = outcome
        print('  state=%-10s %s' % (state, LABEL[outcome['kind']]))
    print('  结论：未注册事件在任何状态下都先被 UnknownEventError 拦截，'
          '终态也不例外（第 0 层校验先于终态拦截）。')
    print()

    print('== B. 已注册但不适用的事件 × 各可达状态 ==')
    per_state = {}
    for state in order:
        raises, ignores, stays, transitions = [], [], [], []
        for event in KNOWN_EVENTS:
            outcome = matrix[(state, event)]
            kind = outcome['kind']
            if kind == 'raise':
                raises.append(event)
            elif kind == 'ignored':
                ignores.append(event)
            elif kind == 'stay':
                stays.append(event)
            else:
                transitions.append('%s→%s' % (event, outcome['target']))
        per_state[state] = {
            'transitions': transitions, 'stay': stays,
            'ignored': ignores, 'raise': raises,
        }
        print('  [%s]' % state)
        print('    合法转移：%s' % (', '.join(transitions) or '无'))
        if stays:
            print('    自循环 stay：%s' % ', '.join(stays))
        print('    静默忽略：%s' % (', '.join(ignores) or '无'))
        print('    抛 InvalidTransitionError：%s' % (', '.join(raises) or '无'))
    print()

    print('== C. 不可达状态 AUDITING 内的非法事件（白盒探针）==')
    auditing = {}
    for event in ('pay', 'deliver', 'refund_done'):
        machine = poke_state('AUDITING')
        outcome = fire_and_classify(machine, event)
        auditing[event] = outcome
        print('  AUDITING + %-12s %s' % (event, LABEL[outcome['kind']]))
    print('  结论：AUDITING 区块内部对未处理事件同样抛错，但该状态运行时不可达。')
    print()

    report = {
        'unknown_event_layer': unknown_layer,
        'per_state': per_state,
        'auditing_probe': auditing,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, 'illegal_events.json'), 'w',
              encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print('结果已写入 analysis/out/illegal_events.json')


if __name__ == '__main__':
    main()
