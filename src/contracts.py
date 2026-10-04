"""违反隐式约定时抛出的统一异常。

每个约定都有稳定的编号（S1.. / O1.. / R1.. / T1..），
断言信息里带上编号，测试和变异验证可以精确定位是哪条约定被破坏。
"""


class ContractViolation(AssertionError):
    """可执行约定被违反。"""


def check(condition, convention_id, message):
    """所有约定断言的唯一入口。"""
    if not condition:
        raise ContractViolation(f"[{convention_id}] {message}")
