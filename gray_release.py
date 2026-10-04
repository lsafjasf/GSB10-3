"""灰度流量切分库（仅依赖标准库）。

判定规则与优先级（从高到低）：
1. 用户标识缺失（None 或空字符串）-> 直接返回 False，不进入灰度。
   缺失标识无法稳定分桶，宁可不放量，结果可复现。
2. 白名单命中 -> 直接返回 True，无视比例（包括比例为 0 的情况）。
3. 按比例分桶 -> 对 sha256(salt + ":" + user_id) 取模落入 [0, BUCKETS)，
   bucket < percentage / 100 * BUCKETS 则命中。

稳定性保证：判定只依赖 (salt, user_id, percentage, whitelist)，
不依赖时间、随机数、进程状态，因此同一用户多次判定结果一致，
且跨进程、跨机器可复现（sha256 是确定性的）。
"""

import hashlib

BUCKETS = 10000  # 分桶粒度：支持 0.01% 精度的比例


def _bucket(user_id, salt):
    """将用户标识稳定映射到 [0, BUCKETS) 的桶号。"""
    key = "{}:{}".format(salt, user_id).encode("utf-8")
    digest = hashlib.sha256(key).hexdigest()
    return int(digest, 16) % BUCKETS


def in_gray(user_id, percentage, whitelist=frozenset(), salt="gray"):
    """判断用户是否进入灰度（新版本）。

    参数:
        user_id: 用户标识，None 或空字符串视为缺失。
        percentage: 放量比例，0~100，支持小数（如 0.5 表示 0.5%）。
        whitelist: 白名单集合，命中即放量，优先级高于比例。
        salt: 实验盐值；不同实验用不同盐值可避免流量相互干扰。

    返回:
        bool，同一组参数下结果恒定。
    """
    if percentage < 0 or percentage > 100:
        raise ValueError("percentage 必须在 [0, 100] 内，得到: %r" % (percentage,))

    # 优先级 1：标识缺失，稳定地拒绝
    if user_id is None or user_id == "":
        return False

    # 优先级 2：白名单命中，无视比例
    if user_id in whitelist:
        return True

    # 优先级 3：按比例分桶
    if percentage <= 0:
        return False
    if percentage >= 100:
        return True
    return _bucket(user_id, salt) < percentage / 100.0 * BUCKETS
