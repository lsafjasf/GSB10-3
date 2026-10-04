"""实验 3：非法事件处理穷举矩阵。

对 12 个声明状态 × 14 个已知事件 + 2 个未知事件逐一探测：
  - 可达状态：用合法事件序列驾驶到该状态后探测（真实运行路径）
  - 终态：驾驶进入后探测（验证"终态静默忽略"与"未知事件仍抛错"的优先级）
  - AUDITING / ARCHIVED：运行时不可达，用 tamper_to 直接改写 .state 探测
    （等价于遗留脏数据或外部改写把订单置于该状态的情形）

输出 experiments/results/03_illegal.json + 终端打印逐状态结论。
"""
import json
import os

from common import (
    ALL_STATES, KNOWN_EVENTS, TERMINAL_STATES, drive_to, fresh, probe, tamper_to,
)

HERE = os.path.dirname(__file__)

# 驾驶到终态的合法路径
TERMINAL_PATHS = {
    'CANCELLED': ['cancel'],
    'CLOSED':    ['pay', 'stock_ok', 'pick_done', 'ship', 'deliver', 'close'],
    'REFUNDED':  ['pay', 'stock_out', 'refund_done'],
}

UNKNOWN_EVENTS = ['flag_risk', 'migrate', 'not_an_event']


def machine_in(state):
    if state in TERMINAL_PATHS:
        m = fresh('TERM')
        for ev in TERMINAL_PATHS[state]:
            m.fire(ev)
        assert m.state == state
        return m
    try:
        return drive_to(state)
    except KeyError:
        return tamper_to(state)  # AUDITING / ARCHIVED：运行时不可达


def classify(obs):
    k = obs['kind']
    if k == 'transition':
        return 'TRANSITION'
    if k == 'stay':
        return 'STAY'
    if k == 'ignored':
        return 'IGNORED'
    return k  # UnknownEventError / InvalidTransitionError


def main():
    matrix = {}
    for state in ALL_STATES:
        row = {}
        for ev in list(KNOWN_EVENTS) + UNKNOWN_EVENTS:
            m = machine_in(state)
            obs = probe(m, ev)
            row[ev] = {'result': classify(obs), 'to': obs['to']}
            # SHIPPED timeout 的累计语义单独说明，矩阵里只记首次结果
        matrix[state] = row

    out = os.path.join(HERE, 'results', '03_illegal.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(matrix, f, ensure_ascii=False, indent=2)

    # 终端打印：每个状态的非"抛 InvalidTransitionError"行为 + 统计
    for state in ALL_STATES:
        row = matrix[state]
        special = {e: r for e, r in row.items() if r['result'] != 'InvalidTransitionError'}
        n_illegal = sum(1 for r in row.values() if r['result'] == 'InvalidTransitionError')
        print('== %s ==' % state)
        for ev, r in sorted(special.items()):
            print('   %-15s -> %s' % (ev, r['result'] + ('(%s)' % r['to'] if r['result'] == 'TRANSITION' else '')))
        print('   其余 %d 个事件 -> InvalidTransitionError' % n_illegal)
        print()
    print('结果已写入', os.path.relpath(out))


if __name__ == '__main__':
    main()
