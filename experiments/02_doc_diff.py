"""实验 2：文档 vs 实现 逐条差异复现。

每条差异 = 一个场景：按文档预期应该发生什么 vs 实际观察到什么。
输出 experiments/results/02_doc_diff.json，全部场景可重复运行。
"""
import json
import os

from common import (
    MAX_SHIP_TIMEOUTS, drive_to, fresh, probe,
    UnknownEventError, InvalidTransitionError,
)

HERE = os.path.dirname(__file__)
SCENARIOS = []


def scenario(doc_says, code_ref):
    def deco(fn):
        SCENARIOS.append((fn.__name__, doc_says, code_ref, fn))
        return fn
    return deco


@scenario('PAID --stock_out--> PAID（缺货等待补货，状态不变）',
          'fire() PAID 分支, legacy_order_machine.py:172-174')
def d1():
    m = drive_to('PAID')
    return probe(m, 'stock_out')


@scenario('PAID --cancel--> CANCELLED（用户取消）',
          'fire() PAID 分支, legacy_order_machine.py:175-177')
def d2():
    m = drive_to('PAID')
    return probe(m, 'cancel')


@scenario('PICKING --cancel--> CANCELLED（用户取消）',
          'fire() PICKING 分支, legacy_order_machine.py:192-194')
def d3():
    m = drive_to('PICKING')
    return probe(m, 'cancel')


@scenario('PACKED --cancel--> CANCELLED（用户取消）',
          'fire() PACKED 分支, legacy_order_machine.py:203-204')
def d4():
    m = drive_to('PACKED')
    return probe(m, 'cancel')


@scenario('SHIPPED --timeout--> DELIVERED（超时系统自动签收，一次即签收）',
          'fire() SHIPPED 分支 + MAX_SHIP_TIMEOUTS, legacy_order_machine.py:213-219,63')
def d5():
    m = drive_to('SHIPPED')
    obs = [probe(m, 'timeout') for _ in range(MAX_SHIP_TIMEOUTS)]
    return {'kind': 'sequence', 'steps': obs,
            'note': '连续 %d 次 timeout 才签收；前 %d 次是自循环'
                    % (MAX_SHIP_TIMEOUTS, MAX_SHIP_TIMEOUTS - 1)}


@scenario('SHIPPED --cancel--> （报错）已发货不允许取消',
          'fire() SHIPPED 分支, legacy_order_machine.py:220-222')
def d6():
    m = drive_to('SHIPPED')
    return probe(m, 'cancel')


@scenario('DELIVERED --return_request--> REFUNDING（用户发起退货）',
          'fire() DELIVERED 分支 + _legacy_v1_returns, legacy_order_machine.py:237-239,88')
def d7():
    m = drive_to('DELIVERED')
    return probe(m, 'return_request')


@scenario('CREATED --flag_risk--> AUDITING（命中风控转人工审核）',
          'KNOWN_EVENTS, legacy_order_machine.py:26-41（flag_risk 已移除）')
def d8():
    m = fresh()
    return probe(m, 'flag_risk')


@scenario('AUDITING --audit_pass--> CLOSED（风控审核通过）',
          'fire() AUDITING 分支, legacy_order_machine.py:265-270（运行时不可达）')
def d9():
    # 无法通过合法事件进入 AUDITING：先发 audit_pass 看真实入口行为
    m = fresh()
    return probe(m, 'audit_pass')


@scenario('REFUNDING --refund_approve--> REFUND_APPROVED（客服审批通过）',
          'fire() REFUNDING 分支, legacy_order_machine.py:252-255（显式拒绝）')
def d10():
    m = drive_to('REFUNDING')
    return probe(m, 'refund_approve')


@scenario('文档状态清单含 REFUND_APPROVED（退款已审批，等待打款）',
          'ALL_STATES, legacy_order_machine.py:47-60（该状态不存在）')
def d11():
    from legacy.legacy_order_machine import ALL_STATES
    exists = 'REFUND_APPROVED' in ALL_STATES
    return {'kind': 'fact', 'refund_approved_in_code_states': exists,
            'note': 'REFUND_APPROVED %s代码状态清单' % ('存在于' if exists else '不存在于')}


@scenario('文档未记载：PAID --timeout 自循环 / SHIPPED 重复 pay 静默忽略 等',
          'fire() PAID/SHIPPED 分支, legacy_order_machine.py:178-180,223-225')
def d12():
    m1 = drive_to('PAID')
    a = probe(m1, 'timeout')
    m2 = drive_to('SHIPPED')
    b = probe(m2, 'pay')
    m3 = drive_to('DELIVERED')
    c = probe(m3, 'deliver')
    return {'kind': 'sequence', 'steps': [a, b, c],
            'note': '三条实现里存在、文档完全没有的转移'}


@scenario('文档未记载：CREATED pay 带非正金额会被拒绝',
          'fire() CREATED 分支, legacy_order_machine.py:152-157')
def d13():
    m1 = fresh()
    a = probe(m1, 'pay', amount=0)
    m2 = fresh()
    b = probe(m2, 'pay', amount=-5)
    m3 = fresh()
    c = probe(m3, 'pay', amount=99.9)
    return {'kind': 'sequence', 'steps': [a, b, c],
            'note': 'amount<=0 抛 InvalidTransitionError；正金额正常转移'}


@scenario('文档未记载：DELIVERED --migrate--> ARCHIVED 归档分支',
          'fire() DELIVERED 分支, legacy_order_machine.py:240-243（死代码）')
def d14():
    m = drive_to('DELIVERED')
    return probe(m, 'migrate')  # migrate 不在 KNOWN_EVENTS，第 0 层被挡


def main():
    results = []
    for name, doc_says, code_ref, fn in SCENARIOS:
        obs = fn()
        results.append({'id': name, 'doc_says': doc_says,
                        'code_ref': code_ref, 'observed': obs})
    out = os.path.join(HERE, 'results', '02_doc_diff.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    for r in results:
        print('【%s】' % r['id'])
        print('  文档说 : %s' % r['doc_says'])
        print('  代码位置: %s' % r['code_ref'])
        obs = r['observed']
        if obs.get('kind') == 'sequence':
            for s in obs['steps']:
                print('  实测   : %s' % _fmt(s))
            print('  备注   : %s' % obs['note'])
        elif obs.get('kind') == 'fact':
            print('  实测   : %s' % obs['note'])
        else:
            print('  实测   : %s' % _fmt(obs))
        print()
    print('结果已写入', os.path.relpath(out))


def _fmt(o):
    if o['kind'] in ('UnknownEventError', 'InvalidTransitionError'):
        return '%s --%s--> 抛 %s' % (o['from'], o['event'], o['kind'])
    if o['kind'] == 'ignored':
        return '%s --%s--> 静默忽略（状态不变，仅记 ignored 账本）' % (o['from'], o['event'])
    if o['kind'] == 'stay':
        extra = '（ship_timeouts=%s）' % o.get('ship_timeouts') if 'ship_timeouts' in o else ''
        return '%s --%s--> 自循环，状态不变%s' % (o['from'], o['event'], extra)
    return '%s --%s--> %s' % (o['from'], o['event'], o['to'])


if __name__ == '__main__':
    main()
