"""实验 1：状态清单 + 事件清单 + 可达性 BFS。

输出 experiments/results/01_static.json：
  - declared_states / declared_terminal / declared_events：代码常量
  - reachable_states：从 CREATED 出发、只发 KNOWN_EVENTS 能到达的状态（BFS）
  - unreachable_states：ALL_STATES 中运行时不可达的状态
  - bfs_edges：BFS 实际走过的 (from, event, to) 边（可达性证据）
不依赖文档，全部从运行实例观察。
"""
import json
import os
from collections import deque

from common import (
    ALL_STATES, KNOWN_EVENTS, TERMINAL_STATES, fresh, probe,
)

HERE = os.path.dirname(__file__)


def bfs():
    """对每个状态全新实例逐个发事件；SHIPPED 的 timeout 需要连发才能看到
    DELIVERED 分支，故对每个目标状态重复探测直到状态不再变化。"""
    start = fresh()
    reachable = {start.state}
    edges = []
    q = deque([start.state])
    while q:
        src = q.popleft()
        if src in TERMINAL_STATES:
            continue
        for ev in KNOWN_EVENTS:
            # 每个 (state, event) 组合用一台全新机器，避免互相污染。
            m = _make_in(src)
            r = probe(m, ev)
            if r['kind'] in ('transition', 'stay'):
                edges.append((src, ev, r['to'], r['kind']))
                if r['to'] not in reachable:
                    reachable.add(r['to'])
                    q.append(r['to'])
                # SHIPPED timeout：连发看累计分支（最多 MAX_SHIP_TIMEOUTS 次）
                if src == 'SHIPPED' and ev == 'timeout' and r['kind'] == 'stay':
                    for _ in range(5):
                        r2 = probe(m, ev)
                        edges.append((r2['from'], ev, r2['to'], r2['kind']))
                        if r2['kind'] == 'transition':
                            if r2['to'] not in reachable:
                                reachable.add(r2['to'])
                                q.append(r2['to'])
                            break
    return reachable, edges


def _make_in(state):
    from common import drive_to
    try:
        return drive_to(state, order_id='BFS')
    except KeyError:
        # BFS 只会请求可达状态，这里兜底直接改写（理论上用不到）
        from common import tamper_to
        return tamper_to(state, order_id='BFS')


def main():
    reachable, edges = bfs()
    result = {
        'declared_states': list(ALL_STATES),
        'declared_terminal': list(TERMINAL_STATES),
        'declared_events': list(KNOWN_EVENTS),
        'initial_state': 'CREATED',
        'reachable_states': sorted(reachable),
        'unreachable_states_declared': sorted(set(ALL_STATES) - reachable),
        'reachable_but_not_declared': sorted(reachable - set(ALL_STATES)),
        'bfs_edges': [
            {'from': a, 'event': b, 'to': c, 'kind': k} for (a, b, c, k) in edges
        ],
    }
    out = os.path.join(HERE, 'results', '01_static.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print('== 代码声明 ==')
    print('ALL_STATES      :', ', '.join(ALL_STATES))
    print('TERMINAL_STATES :', ', '.join(TERMINAL_STATES))
    print('KNOWN_EVENTS    :', ', '.join(KNOWN_EVENTS))
    print()
    print('== BFS 可达性（起点 CREATED，只发已知事件）==')
    print('可达   :', ', '.join(result['reachable_states']))
    print('不可达 :', ', '.join(result['unreachable_states_declared']))
    print('BFS 观察到的边：')
    for e in result['bfs_edges']:
        tag = '自循环' if e['kind'] == 'stay' else '转移'
        print('  %-9s --%-15s--> %-9s [%s]' % (e['from'], e['event'], e['to'], tag))
    print('\n结果已写入', os.path.relpath(out))


if __name__ == '__main__':
    main()
