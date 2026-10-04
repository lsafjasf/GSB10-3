"""
legacy_order_machine.py —— 订单履约状态机（遗留实现）。

本模块自 2016 年起在线上逐步打补丁演化，分支较多。业务文档见
``docs/state_machine_spec.md``（最后更新于 2019-05，已明显滞后于实现；
改动任何分支前请先用 analysis/ 下的实验脚本实测，不要只信文档）。

公开接口：
    LegacyOrderMachine(order_id)   初始状态固定为 CREATED
    .fire(event, **ctx)            触发一个事件，返回转移后的状态名
    .state                         当前状态名（字符串）
    .history                       [(from_state, event, to_state), ...]
    .ignored                       [(state, event), ...] 被静默忽略的事件
    .ship_timeouts                 SHIPPED 下 timeout 事件累计次数

异常体系：
    UnknownEventError      事件名不在 KNOWN_EVENTS 中（任何状态下先做这层校验）
    InvalidTransitionError 事件合法、但当前状态不接受该事件（非终态、且未被
                           显式忽略时抛出）
"""

from __future__ import annotations

# 系统认可的全部事件。fire() 的第一道闸门：不在此清单中的事件名
# 无论当前处于什么状态都会立刻抛 UnknownEventError。
KNOWN_EVENTS = (
    'pay',
    'cancel',
    'timeout',
    'stock_ok',
    'stock_out',
    'pick_done',
    'ship',
    'deliver',
    'close',
    'return_request',
    'refund_approve',
    'refund_done',
    'audit_pass',
    'audit_fail',
)

# 终态：进入后不再接受任何业务转移，所有已知事件一律静默忽略。
TERMINAL_STATES = ('CLOSED', 'CANCELLED', 'REFUNDED')

# 历史上出现过的全部状态，包括已经不可达的 AUDITING / ARCHIVED。
ALL_STATES = (
    'CREATED',
    'PAID',
    'PICKING',
    'PACKED',
    'SHIPPED',
    'DELIVERED',
    'REFUNDING',
    'CLOSED',
    'CANCELLED',
    'REFUNDED',
    'AUDITING',
    'ARCHIVED',
)

# SHIPPED 状态下 timeout 连续累计达到该次数后自动签收。
MAX_SHIP_TIMEOUTS = 3


class UnknownEventError(Exception):
    """事件名不在 KNOWN_EVENTS 中。"""


class InvalidTransitionError(Exception):
    """事件合法，但在当前状态下不允许（非终态、且未被静默忽略）。"""


class LegacyOrderMachine:
    """订单履约状态机。构造后状态恒为 CREATED。"""

    def __init__(self, order_id):
        self.order_id = order_id
        self.state = 'CREATED'
        # 成功转移（含自循环 stay）都记录进 history。
        self.history = []
        # 被静默忽略的事件单独记录，排查问题时用。
        self.ignored = []
        # SHIPPED 下 timeout 的累计计数，达到 MAX_SHIP_TIMEOUTS 后自动签收。
        self.ship_timeouts = 0
        # v1 退货流程开关：2019 年新退货中心上线后强制关闭，保留至今恒为 False，
        # DELIVERED 状态里依赖它的分支实际上已经死掉，只是没人敢删。
        self._legacy_v1_returns = False

    # ------------------------------------------------------------------ #
    # 内部辅助
    # ------------------------------------------------------------------ #
    def _go(self, target, event):
        """执行一次正式转移，并写入 history。"""
        prev = self.state
        self.state = target
        self.history.append((prev, event, target))
        return target

    def _stay(self, event):
        """状态不变，但属于正常业务处理，写入 history（自循环）。"""
        self.history.append((self.state, event, self.state))
        return self.state

    def _ignore(self, event):
        """静默忽略：状态不变、不进 history、只记 ignored 账本。"""
        self.ignored.append((self.state, event))
        return self.state

    def _illegal(self, event):
        """当前状态不接受该事件：抛 InvalidTransitionError。"""
        raise InvalidTransitionError(
            'event %r not allowed in state %r (order_id=%r)'
            % (event, self.state, self.order_id)
        )

    def snapshot(self):
        """返回当前现场快照，供日志/排查使用。"""
        return {
            'order_id': self.order_id,
            'state': self.state,
            'ship_timeouts': self.ship_timeouts,
            'history': list(self.history),
            'ignored': list(self.ignored),
        }

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def fire(self, event, **ctx):
        """触发 ``event``，返回转移后的状态名。

        未知事件永远先抛 UnknownEventError；终态对已知事件一律静默忽略；
        其余状态的分支全部在本方法体内以 if/elif 展开。
        """
        # ---- 第 0 层：事件名校验（先于一切状态判断）------------------ #
        if event not in KNOWN_EVENTS:
            raise UnknownEventError(
                'unknown event %r (order_id=%r)' % (event, self.order_id)
            )

        # ---- 终态统一拦截 ------------------------------------------- #
        if self.state in TERMINAL_STATES:
            # 2018 年为兼容物流/支付网关的重复回调加入：终态对任何已知
            # 事件都静默吞掉，避免重放任务失败。
            return self._ignore(event)

        # ============================================================== #
        # CREATED：已下单、未支付
        # ============================================================== #
        if self.state == 'CREATED':
            if event == 'pay':
                # 支付金额必须为正；异常金额不做转移，直接按非法事件拒绝。
                amount = ctx.get('amount')
                if amount is not None and amount <= 0:
                    return self._illegal(event)
                return self._go('PAID', event)
            if event == 'cancel':
                # 未支付订单直接取消。
                return self._go('CANCELLED', event)
            if event == 'timeout':
                # 下单 30 分钟未支付，系统超时自动取消。
                return self._go('CANCELLED', event)
            return self._illegal(event)

        # ============================================================== #
        # PAID：已支付、等待仓库
        # ============================================================== #
        if self.state == 'PAID':
            if event == 'stock_ok':
                return self._go('PICKING', event)
            if event == 'stock_out':
                # 2021-03 起缺货不再"等待补货"，直接发起退款（文档未更新）。
                return self._go('REFUNDING', event)
            if event == 'cancel':
                # 2020-07 财务要求：已付款订单取消必须先走退款，禁止直达 CANCELLED。
                return self._go('REFUNDING', event)
            if event == 'timeout':
                # 已支付订单上的 timeout 来自旧任务重放，视为无害，自循环记录。
                return self._stay(event)
            return self._illegal(event)

        # ============================================================== #
        # PICKING：仓库拣货中
        # ============================================================== #
        if self.state == 'PICKING':
            if event == 'pick_done':
                return self._go('PACKED', event)
            if event == 'stock_out':
                # 拣货中发现缺货，同样直接转退款。
                return self._go('REFUNDING', event)
            if event == 'cancel':
                # 与 PAID 同理：已付款，取消必须先进退款流程。
                return self._go('REFUNDING', event)
            return self._illegal(event)

        # ============================================================== #
        # PACKED：已打包、待发货
        # ============================================================== #
        if self.state == 'PACKED':
            if event == 'ship':
                return self._go('SHIPPED', event)
            if event == 'cancel':
                return self._go('REFUNDING', event)
            return self._illegal(event)

        # ============================================================== #
        # SHIPPED：已发货、在途
        # ============================================================== #
        if self.state == 'SHIPPED':
            if event == 'deliver':
                return self._go('DELIVERED', event)
            if event == 'timeout':
                # 物流轨迹超时：连续累计 MAX_SHIP_TIMEOUTS 次才自动签收，
                # 中间几次只累计计数、状态不变（文档声称"超时立即签收"）。
                self.ship_timeouts += 1
                if self.ship_timeouts >= MAX_SHIP_TIMEOUTS:
                    return self._go('DELIVERED', event)
                return self._stay(event)
            if event == 'cancel':
                # 已发货无法取消；物流网关失败重放会反复发 cancel，静默吞掉。
                return self._ignore(event)
            if event == 'pay':
                # 支付网关重复回调，静默吞掉。
                return self._ignore(event)
            return self._illegal(event)

        # ============================================================== #
        # DELIVERED：已签收
        # ============================================================== #
        if self.state == 'DELIVERED':
            if event == 'close':
                return self._go('CLOSED', event)
            if event == 'deliver':
                # 物流重复签收回调，静默吞掉。
                return self._ignore(event)
            if event == 'return_request' and self._legacy_v1_returns:
                # v1 退货流程入口；开关 2019 年起恒为 False，该分支已死。
                return self._go('REFUNDING', event)
            if event == 'migrate':
                # 归档迁移试验功能：事件名从未登记进 KNOWN_EVENTS，
                # 会在第 0 层校验被挡下，本分支永远无法执行，ARCHIVED 也不可达。
                return self._go('ARCHIVED', event)
            return self._illegal(event)

        # ============================================================== #
        # REFUNDING：退款中（自动化一步到位，无需人工审批）
        # ============================================================== #
        if self.state == 'REFUNDING':
            if event == 'refund_done':
                return self._go('REFUNDED', event)
            if event == 'refund_approve':
                # 2022-11 退款审批改为系统自动完成；保留该分支的显式拒绝，
                # 上游若还在发旧事件会立即收到报错。
                return self._illegal(event)
            return self._illegal(event)

        # ============================================================== #
        # AUDITING：风控人工审核（整个状态区块已不可达）
        #
        # 2022 年风控审核接口下线后，系统中不再有任何事件会把订单置为
        # AUDITING（旧的 flag_risk 事件也已从 KNOWN_EVENTS 移除）。
        # 下面的分支本身仍可执行，但只能通过直接改写 .state 进入，运行时不可达。
        # ============================================================== #
        if self.state == 'AUDITING':
            if event == 'audit_pass':
                return self._go('CLOSED', event)
            if event == 'audit_fail':
                return self._go('CANCELLED', event)
            return self._illegal(event)

        # ---- 防御性兜底：state 被外部改成未知值 ---------------------- #
        raise InvalidTransitionError(
            'machine in unknown state %r (order_id=%r)'
            % (self.state, self.order_id)
        )
