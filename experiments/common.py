"""公共探针工具：只读调用被测状态机，绝不修改 legacy/ 下的代码。

被测对象：legacy/legacy_order_machine.py（LegacyOrderMachine）
所有探针通过两种"驾驶"方式观察行为：
  1. drive_to(state)  —— 用合法事件序列把状态机开到目标状态（模拟真实运行）
  2. tamper_to(state) —— 直接改写 .state（仅用于探测运行时不可达的分支，
                          等价于"如果有遗留数据/外部改写把订单置于该状态"）
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from legacy.legacy_order_machine import (  # noqa: E402
    LegacyOrderMachine,
    KNOWN_EVENTS,
    TERMINAL_STATES,
    ALL_STATES,
    MAX_SHIP_TIMEOUTS,
    UnknownEventError,
    InvalidTransitionError,
)

# 从 CREATED 出发、只使用合法事件到达各非终态的最短路径（BFS 手工验证过）。
# SHIPPED 的 timeout 前两次是自循环，用 deliver 直达 DELIVERED。
PATHS = {
    'CREATED':  [],
    'PAID':     ['pay'],
    'PICKING':  ['pay', 'stock_ok'],
    'PACKED':   ['pay', 'stock_ok', 'pick_done'],
    'SHIPPED':  ['pay', 'stock_ok', 'pick_done', 'ship'],
    'DELIVERED':['pay', 'stock_ok', 'pick_done', 'ship', 'deliver'],
    'REFUNDING':['pay', 'stock_out'],
}


def fresh(order_id='PROBE'):
    return LegacyOrderMachine(order_id)


def drive_to(state, order_id='PROBE'):
    """用合法事件序列把状态机开到指定非终态，返回实例。"""
    m = fresh(order_id)
    for ev in PATHS[state]:
        m.fire(ev)
    assert m.state == state, 'drive_to(%s) 失败，停在 %s' % (state, m.state)
    return m


def tamper_to(state, order_id='TAMPER'):
    """直接把 .state 改成目标值（探测运行时不可达分支用）。"""
    m = fresh(order_id)
    m.state = state
    return m


def probe(machine, event, **ctx):
    """对给定实例触发一次事件，返回结构化观察结果（不吞异常）。"""
    before = machine.state
    hist_before = len(machine.history)
    ign_before = len(machine.ignored)
    try:
        ret = machine.fire(event, **ctx)
    except UnknownEventError as e:
        return {'kind': 'UnknownEventError', 'from': before, 'event': event,
                'to': before, 'detail': str(e)}
    except InvalidTransitionError as e:
        return {'kind': 'InvalidTransitionError', 'from': before, 'event': event,
                'to': before, 'detail': str(e)}
    delta_hist = machine.history[hist_before:]
    delta_ign = machine.ignored[ign_before:]
    if machine.state != before:
        kind = 'transition'
    elif delta_hist:
        kind = 'stay'            # 自循环：状态不变但写入 history
    elif delta_ign:
        kind = 'ignored'         # 静默忽略：只记 ignored 账本
    else:
        kind = 'noop'
    return {'kind': kind, 'from': before, 'event': event, 'to': machine.state,
            'returned': ret, 'ship_timeouts': machine.ship_timeouts,
            'history_tail': list(delta_hist), 'ignored_tail': list(delta_ign)}
