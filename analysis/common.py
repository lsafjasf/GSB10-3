"""
common.py —— 各实验脚本共用的驱动工具。

只通过公开接口 fire() 操作被测状态机，不修改 legacy/ 下任何代码；
为验证不可达状态的分支，允许在实验机实例上直接改写 .state（白盒探针，
报告中会明确标注该手段）。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from legacy.legacy_order_machine import (  # noqa: E402
    ALL_STATES,
    KNOWN_EVENTS,
    TERMINAL_STATES,
    InvalidTransitionError,
    LegacyOrderMachine,
    UnknownEventError,
)

# 用于探测"未注册事件"行为的假名（确认它确实不在 KNOWN_EVENTS 中）。
BOGUS_EVENT = 'definitely_not_a_registered_event'
assert BOGUS_EVENT not in KNOWN_EVENTS

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')


def fresh_machine():
    """每台实验机都从初始状态 CREATED 出发。"""
    return LegacyOrderMachine(order_id='EXPERIMENT')


def poke_state(state):
    """白盒探针：直接把机器放到指定状态（用于验证不可达状态里的分支）。"""
    machine = fresh_machine()
    machine.state = state
    return machine


def fire_and_classify(machine, event, **ctx):
    """触发一个事件并分类结果，不吞掉任何异常之外的副作用。

    返回 dict：
      kind = 'transition'  状态改变（target 给出新状态）
           | 'stay'        正常自循环（进 history，状态不变）
           | 'ignored'     静默忽略（只进 ignored 账本）
           | 'raise'       抛 InvalidTransitionError
           | 'unknown'     抛 UnknownEventError
    """
    before = machine.state
    ignored_before = len(machine.ignored)
    history_before = len(machine.history)
    try:
        after = machine.fire(event, **ctx)
    except InvalidTransitionError as exc:
        return {
            'kind': 'raise',
            'error': 'InvalidTransitionError',
            'state': before,
            'detail': str(exc),
        }
    except UnknownEventError as exc:
        return {
            'kind': 'unknown',
            'error': 'UnknownEventError',
            'state': before,
            'detail': str(exc),
        }
    if len(machine.ignored) > ignored_before:
        kind = 'ignored'
    elif after != before:
        kind = 'transition'
    elif len(machine.history) > history_before:
        kind = 'stay'
    else:
        kind = 'ignored'
    return {'kind': kind, 'state': before, 'target': after}


def discover_reachable(probe_events=None):
    """从 CREATED 做 BFS：每个已知状态 × 每个已知事件全部打一遍。

    返回：
      matrix   {(state, event): outcome_dict}
      paths    {state: [从 CREATED 出发的事件序列]}
      order    BFS 发现顺序
    """
    probe_events = list(probe_events) if probe_events is not None else list(KNOWN_EVENTS)
    matrix = {}
    paths = {'CREATED': []}
    order = []
    queue = ['CREATED']
    seen = {'CREATED'}
    while queue:
        state = queue.pop(0)
        order.append(state)
        for event in probe_events:
            machine = fresh_machine()
            for step in paths[state]:
                machine.fire(step)
            result = fire_and_classify(machine, event)
            matrix[(state, event)] = result
            if result['kind'] == 'transition':
                target = result['target']
                if target not in seen:
                    seen.add(target)
                    paths[target] = paths[state] + [event]
                    queue.append(target)
    return matrix, paths, order


def reachable_states():
    _matrix, paths, order = discover_reachable()
    return set(order), paths


def probe_dead_state(state, events=None):
    """白盒：把机器直接放进 state，逐个事件探测其内部分支行为。"""
    events = list(events) if events is not None else list(KNOWN_EVENTS)
    results = {}
    for event in events:
        machine = poke_state(state)
        results[(state, event)] = fire_and_classify(machine, event)
    machine = poke_state(state)
    results[(state, BOGUS_EVENT)] = fire_and_classify(machine, BOGUS_EVENT)
    return results
