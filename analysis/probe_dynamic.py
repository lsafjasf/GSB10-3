"""
probe_dynamic.py —— 动态枚举真实转移行为。

做法：从初始状态 CREATED 做 BFS，对每个可达状态，用全新机器沿 BFS 路径
就位后，把 KNOWN_EVENTS 里每个事件各打一遍，按返回值 / 异常 / history /
ignored 账本分类结果。另对静态不可达状态（AUDITING）与未注册事件做
专项探测。

输出 analysis/out/dynamic.json 并打印矩阵。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402
    ALL_STATES,
    BOGUS_EVENT,
    KNOWN_EVENTS,
    OUT_DIR,
    discover_reachable,
    fire_and_classify,
    poke_state,
)

SYMBOL = {
    'transition': '→',
    'stay': '↺stay',
    'ignored': '∅ignore',
    'raise': '✗raise',
    'unknown': '?unknown',
}


def describe(outcome):
    if outcome['kind'] == 'transition':
        return '→' + outcome['target']
    return SYMBOL[outcome['kind']]


def main():
    matrix, paths, order = discover_reachable()

    # ---- 可达状态 × 已知事件矩阵 ------------------------------------ #
    events = list(KNOWN_EVENTS)
    print('== 动态探测矩阵（行=当前状态，列=事件；→X 转移 / ↺stay 自循环 / '
          '∅ignore 静默忽略 / ✗raise 抛 InvalidTransitionError）==')
    header = '%-11s' % 'STATE' + ''.join('%-14s' % e for e in events)
    print(header)
    for state in order:
        row = '%-11s' % state
        for event in events:
            row += '%-14s' % describe(matrix[(state, event)])
        print(row)
    print()

    # ---- 不可达状态的白盒探测 ---------------------------------------- #
    reachable = set(order)
    dead_states = [s for s in ALL_STATES if s not in reachable]
    print('BFS 可达状态（%d 个）：%s' % (len(order), ', '.join(order)))
    print('运行时不可达状态：%s' % (', '.join(dead_states) or '无'))
    print()
    dead_probe = {}
    for state in dead_states:
        machine = poke_state(state)
        print('白盒探针：直接置 state=%r 后逐事件探测' % state)
        for event in events:
            machine = poke_state(state)
            outcome = fire_and_classify(machine, event)
            dead_probe['%s|%s' % (state, event)] = outcome
            if outcome['kind'] in ('transition', 'stay', 'ignored'):
                print('  %-16s %s' % (event, describe(outcome)))
        machine = poke_state(state)
        bogus = fire_and_classify(machine, BOGUS_EVENT)
        dead_probe['%s|%s' % (state, BOGUS_EVENT)] = bogus
        print('  %-16s %s（未注册事件同样先被 UnknownEventError 拦截）'
              % (BOGUS_EVENT, describe(bogus)))
        print()

    # ---- 未注册事件在可达状态下的行为 --------------------------------- #
    print('未注册事件 %r 在部分可达状态下的表现：' % BOGUS_EVENT)
    bogus_results = {}
    for state in ['CREATED', 'SHIPPED', 'CLOSED']:
        machine = poke_state(state) if state != 'CREATED' else None
        if machine is None:
            from common import fresh_machine
            machine = fresh_machine()
        outcome = fire_and_classify(machine, BOGUS_EVENT)
        bogus_results[state] = outcome
        print('  state=%-10s %s' % (state, describe(outcome)))
    print()

    report = {
        'reachable_states': order,
        'unreachable_states': dead_states,
        'bfs_paths': paths,
        'matrix': {'%s|%s' % key: value for key, value in matrix.items()},
        'dead_state_probe': dead_probe,
        'bogus_event_probe': bogus_results,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, 'dynamic.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print('结果已写入 analysis/out/dynamic.json')


if __name__ == '__main__':
    main()
